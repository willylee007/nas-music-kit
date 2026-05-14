import os
import requests
import mimetypes
import urllib.parse
import hashlib
import time
import re
import functools
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, redirect, send_from_directory

from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, USLT, ID3NoHeaderError
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover

app = Flask(__name__, template_folder='.', static_folder='static', static_url_path='/static')

download_executor = ThreadPoolExecutor(max_workers=3)
download_jobs = {}

# 下载目录：支持 ~/ 路径展开
DOWNLOAD_DIR = os.path.expanduser(os.environ.get('DOWNLOAD_DIR', '/music'))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── API 校验 ───────────────────────────────────────────────────

# 项目配置
APP_VERSION = "1.1"
APP_NAME = "NAS 音乐助手"

MK_VERSION = "2025.11.4"
MK_HOSTNAME = "music.gdstudio.xyz"

# 接口请求头
API_HEADERS = {
    'Referer': f'https://{MK_HOSTNAME}/',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'X-Requested-With': 'XMLHttpRequest'
}

# 资源请求头 (用于下载音频)
MEDIA_HEADERS = {
    'User-Agent': API_HEADERS['User-Agent'],
    'Referer': 'https://music.163.com/'
}

def get_signature(id_str: str):
    """
    计算 API 校验参数。
    """
    ver_parts = MK_VERSION.split('.')
    ver_padded = "".join([p.zfill(2) if len(p) == 1 else p for p in ver_parts])
    
    try:
        ts_resp = requests.get("https://www.ximalaya.com/revision/time", timeout=3)
        ts_val = ts_resp.text[:9]
    except:
        ts_val = str(int(time.time() * 1000))[:9]
        
    concat_str = f"{MK_HOSTNAME}|{ver_padded}|{ts_val}|{id_str}"
    h = hashlib.md5(concat_str.encode('utf-8')).hexdigest()
    
    return h[-8:].upper()

def normalize_url(url: str, source: str):
    """
    音频链接规范化。
    """
    if not url: return url
    if url.startswith('//'): url = 'https:' + url
    
    if source == 'kuwo' and 'kuwo.cn' in url:
        import re
        match = re.search(r'://(.*?)(?=\.kuwo\.cn)', url)
        if match:
            subdomain = match.group(1)
            new_subdomain = subdomain.replace('.', '-')
            url = url.replace(f'://{subdomain}.kuwo.cn', f'://{new_subdomain}.kuwo.cn')
            
    return url

# ── 链接解析 ───────────────────────────────────────────────────

def extract_url(text: str):
    """从文本中提取出第一个 URL"""
    match = re.search(r'https?://[^\s\u4e00-\u9fa5)\]]+', text)
    if match:
        return match.group(0).strip()
    return None

def resolve_url(url: str):
    """还原短链接获取真实地址"""
    try:
        # 使用 GET (stream=True) 比 HEAD 更稳定，且只获取 Header 不下载 Body
        resp = requests.get(url, headers=API_HEADERS, allow_redirects=True, timeout=5, stream=True)
        real_url = resp.url
        resp.close() # 及时关闭流连接
        return real_url
    except:
        return url

def parse_music_link(text: str):
    """
    解析分享链接，返回 (source, id)
    支持：网易云 (music.163.com, 163cn.tv), QQ音乐 (y.qq.com), 酷我 (kuwo.cn)
    """
    url = extract_url(text)
    if not url: return None, None
    
    # 还原短链
    if '163cn.tv' in url or 'url.cn' in url:
        url = resolve_url(url)
    
    # 网易云
    if 'music.163.com' in url or '163.com' in url or '163cn.tv' in url:
        if '163cn.tv' in url:
            url = resolve_url(url)
        # 提取 ID：支持 id=123 或 /song/123/ 模式
        m = re.search(r'(?:id=|/song/)(\d+)', url)
        if m: return 'netease', m.group(1)
    
    # QQ 音乐
    if 'y.qq.com' in url:
        # y.qq.com/n/ryqq/songDetail/0039l7is0p99yk
        m = re.search(r'songDetail/([a-zA-Z0-9]+)', url)
        if m: return 'tencent', m.group(1)
    
    # 酷我
    if 'kuwo.cn' in url:
        m = re.search(r'play_detail/(\d+)', url)
        if m: return 'kuwo', m.group(1)
        
    return None, None

# ── API 配置 ───────────────────────────────────────────────────────────────

# 公开接口
API_PUBLIC = 'https://music-api.gdstudio.xyz/api.php'

# 数据接口
API_VIP = 'https://music.gdstudio.xyz/api.php'

# BugPk 接口 (网易云音乐 2)
API_BUGPK = 'https://api.bugpk.com/api/163_music'

def api_cfg(is_vip: bool, source: str = None):
    """返回 (api_base, headers, is_bugpk)"""
    if source == 'netease2':
        return API_BUGPK, {}, True
    if is_vip:
        return API_VIP, {**API_HEADERS}, False
    return API_PUBLIC, {**API_HEADERS}, False

# ── API 适配层 (独立纯净驱动) ───────────────────────────────────────────────
@functools.lru_cache(maxsize=16)
def get_bugpk_handler(track_id, br_or_level):
    """网易云 2 (BugPk) 专线驱动：单次请求获取全部字段，带缓存避免重复请求"""
    lv_map = {'999': 'hires', '740': 'lossless', '320': 'exhigh', '128': 'standard'}
    level = lv_map.get(str(br_or_level), br_or_level)
    try:
        # 使用核心 json 接口，一次性获取元数据、链接、码率、大小、歌词
        resp = requests.get(API_BUGPK, params={'type': 'json', 'ids': track_id, 'level': level}, timeout=10).json()
        # 兼容性处理：部分版本返回 code，部分返回 status；部分有 data 包装，部分没有
        is_success = resp.get('code') == 200 or resp.get('status') == 200
        item = resp.get('data', resp) if is_success else {}
        
        if not item.get('url'): return None
        
        return {
            'url': normalize_url(item.get('url'), 'netease2'),
            'size': item.get('size', ''),
            'lyric': (item.get('lyric', '') + '\n\n' + item.get('tlyric', '')) if item.get('tlyric') else item.get('lyric', ''),
            'pic_url': item.get('pic', ''),
            'level_text': item.get('level', f"{level}音质"),
            # 扩展字段供 workflow 使用
            'name': item.get('name'),
            'artist': item.get('ar_name'),
            'album': item.get('al_name')
        }
    except: return None

def get_gdstudio_handler(source, track_id, br, is_vip, pic_id=None, include_lyric=False):
    """网易云 1 (GD Studio) 专线驱动"""
    base, headers, _ = api_cfg(is_vip, source)
    try:
        # 链接获取
        p = {'types': 'url', 'source': source, 'id': track_id, 'br': br}
        if is_vip: p['s'] = get_signature(track_id)
        u_data = requests.get(base, params=p, headers=headers, timeout=10).json() or {}
        url = normalize_url(u_data if isinstance(u_data, str) else u_data.get('url'), source)
        if not url: return None
        
        sz_b = u_data.get('size', 0) if isinstance(u_data, dict) else 0
        
        l_d = {}
        if include_lyric:
            # 歌词获取
            l_p = {'types': 'lyric', 'source': source, 'id': track_id}
            if is_vip: l_p['s'] = get_signature(track_id)
            l_d = requests.get(base, params=l_p, headers=headers, timeout=5).json() or {}

        # 解析真正的封面图片 URL (强制请求 500x500 尺寸)
        p_id = pic_id or track_id
        sig_p = f"&s={get_signature(p_id)}" if is_vip else ""
        p_u = f"{base}?types=pic&source={source}&id={p_id}&size=500{sig_p}"
        try:
            p_res = requests.get(p_u, headers=headers, timeout=5).json()
            pic_proxy = p_res.get('url') if isinstance(p_res, dict) else None
        except: pic_proxy = None

        # 码率映射为标准中文名
        try: br_val = int(br) if str(br).isdigit() else 999
        except: br_val = 999
        
        if br_val >= 999: level_text = "Hi-Res音质"
        elif br_val >= 740: level_text = "无损音质"
        elif br_val >= 320: level_text = "极高音质"
        elif br_val >= 192: level_text = "较高音质"
        else: level_text = "标准音质"

        return {
            'url': url,
            'size': f"{sz_b / (1024*1024):.2f}MB" if sz_b else "",
            'lyric': (l_d.get('lyric', '') + '\n\n' + l_d.get('tlyric', '')) if l_d.get('tlyric') else l_d.get('lyric', ''),
            'pic_url': pic_proxy,
            'level_text': level_text
        }
    except: return None

def fetch_music_package(source, track_id, br, is_vip, pic_id=None, include_lyric=False):
    """物理隔离分发逻辑"""
    if source == 'netease2':
        return get_bugpk_handler(track_id, br)
    return get_gdstudio_handler(source, track_id, br, is_vip, pic_id, include_lyric)



# ── 工具函数 ───────────────────────────────────────────────────────────────

def _resolve_download_dir(subdir=''):
    subdir = str(subdir or '').strip()
    base_dir = os.path.realpath(DOWNLOAD_DIR)

    if not subdir:
        return base_dir
    if '\x00' in subdir or os.path.isabs(subdir):
        raise ValueError('下载目录无效')

    parts = [part for part in re.split(r'[\\/]+', subdir) if part]
    if any(part == '..' for part in parts):
        raise ValueError('下载目录不能包含 ..')

    target_dir = os.path.realpath(os.path.join(base_dir, *parts))
    if os.path.commonpath([base_dir, target_dir]) != base_dir:
        raise ValueError('下载目录超出允许范围')

    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def write_tags(filepath, ext, title, artist, album, cover_bytes=None, lyric_text=None):
    """
    向音频文件写入元数据标签。
    支持格式：.mp3 (ID3v2) / .flac (Vorbis Comment) / .m4a (MP4 Atoms)
    修改规格：如需支持 .ogg，可在此添加 mutagen.oggvorbis 分支
    """
    try:
        if ext == '.mp3':
            try:
                tags = ID3(filepath)
            except ID3NoHeaderError:
                tags = ID3()

            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artist))
            tags.add(TALB(encoding=3, text=album))
            if cover_bytes:
                tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_bytes))
            if lyric_text:
                tags.add(USLT(encoding=3, lang='zho', desc='', text=lyric_text))
            tags.save(filepath)

        elif ext == '.flac':
            audio = FLAC(filepath)
            audio['title'] = title
            audio['artist'] = artist
            audio['album'] = album
            if lyric_text:
                audio['lyrics'] = lyric_text
            if cover_bytes:
                pic = Picture()
                pic.type = 3
                pic.mime = 'image/jpeg'
                pic.desc = 'Cover'
                pic.data = cover_bytes
                audio.clear_pictures()
                audio.add_picture(pic)
            audio.save()

        elif ext == '.m4a':
            audio = MP4(filepath)
            audio['\xa9nam'] = title
            audio['\xa9ART'] = artist
            audio['\xa9alb'] = album
            if lyric_text:
                audio['\xa9lyr'] = lyric_text
            if cover_bytes:
                audio['covr'] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()

        return True
    except Exception as e:
        print(f"[write_tags] 写入标签失败 ({ext}): {e}")
        return False


# ── 路由 ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', vip_mode=False)


@app.route('/vip')
def vip_index():
    return render_template('index.html', vip_mode=True)
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('.', 'manifest.json')


@app.route('/sw.js')
def serve_sw():
    return send_from_directory('.', 'sw.js', mimetype='application/javascript')


@app.route('/api/cover')
def cover():
    source  = request.args.get('source')
    pic_id  = request.args.get('id')
    is_vip  = request.args.get('vip') == '1'

    if not source or not pic_id:
        return "", 404

    base, headers, is_bugpk = api_cfg(is_vip, source)
    img_url = None
    try:
        # 1. 针对新版网易云 (netease2) 
        if is_bugpk:
            # BugPk 使用综合接口 type=json，支持 ids
            resp = requests.get(base, params={'type': 'json', 'ids': pic_id}, headers=headers, timeout=8)
            data = resp.json()
            if data.get('code') == 200 or data.get('status') == 200:
                d = data.get('data', data)
                img_url = d.get('pic')
        
        # 2. 其他平台或 BugPk 降级
        if not img_url:
            params = {'types': 'pic', 'source': source, 'id': pic_id, 'size': 500}
            if is_vip: params['s'] = get_signature(pic_id)
            
            resp = requests.get(base, params=params, headers=headers, timeout=8)
            data = resp.json()
            
            if isinstance(data, dict):
                img_url = data.get('url') or (data.get('data', {}).get('picimg') if isinstance(data.get('data'), dict) else None)
        
        if img_url:
            # 代理图片，解决移动端 Referer/Mixed Content 问题
            img_resp = requests.get(img_url, headers=MEDIA_HEADERS, timeout=10)
            if img_resp.status_code == 200:
                return img_resp.content, 200, {
                    'Content-Type': img_resp.headers.get('Content-Type', 'image/jpeg'),
                    'Cache-Control': 'public, max-age=86400'  # 缓存 1 天
                }
            
        return "", 404
    except:
        return "", 404


@app.route('/api/search')
def search():
    source  = request.args.get('source', 'netease')
    keyword = request.args.get('name', '')
    page    = request.args.get('pages', 1)
    search_type = request.args.get('search_type', 'song')
    is_vip  = request.args.get('vip') == '1'

    if not keyword:
        return jsonify({'error': 'Keyword is required'}), 400

    search_base_source = 'netease' if search_type == 'album' and source == 'netease2' else source
    base, headers, is_bugpk = api_cfg(is_vip, search_base_source)
    try:
        try:
            count = int(request.args.get('count', 20))
            page = int(page)
        except:
            count, page = 20, 1
            
        search_source = f'{search_base_source}_album' if search_type == 'album' else search_base_source
        if is_bugpk:
            params = {'type': 'search', 'keywords': keyword, 'limit': count, 'offset': (page - 1) * count}
        else:
            params = {'types': 'search', 'source': search_source, 'name': keyword, 'count': count, 'pages': page}
            if is_vip: params['s'] = get_signature(keyword)
            
        resp = requests.get(base, params=params, headers=headers, timeout=10)
        data = resp.json()
        
        results = []
        if is_bugpk:
            if isinstance(data, dict) and (data.get('code') == 200 or data.get('status') == 200):
                raw_songs = data.get('data', {}).get('songs', [])
                for s in raw_songs:
                    results.append({
                        'id': s.get('id'),
                        'name': s.get('name'),
                        'artist': [s.get('artists')] if isinstance(s.get('artists'), str) else s.get('artists', []),
                        'album': s.get('album', ''),
                        'pic_id': s.get('id'),
                        'source': source
                    })
        else:
            if isinstance(data, list):
                results = data[:count]
        
        for item in results:
            item['source'] = search_base_source

        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/preview')
def preview():
    """返回试听链接"""
    source   = request.args.get('source')
    track_id = request.args.get('id')
    is_vip   = request.args.get('vip') == '1'

    if not source or not track_id:
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        pkg = fetch_music_package(source, track_id, br='128', is_vip=is_vip)
        if pkg and pkg.get('url'):
            return jsonify({'url': pkg['url']})
        return jsonify({'error': '无法获取试听链接'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/lyric', methods=['POST'])
def download_lyric_endpoint():
    data     = request.json
    source   = data.get('source')
    track_id = data.get('id')
    name     = data.get('name')
    artist   = data.get('artist', 'Unknown')
    is_vip   = data.get('vip', False)

    if not all([source, track_id, name]):
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        target_dir = _resolve_download_dir(data.get('subdir', ''))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # 1. 获取标准化数据 (含歌词)
    pkg = fetch_music_package(source, track_id, br='128', is_vip=is_vip, pic_id=data.get('pic_id'), include_lyric=True)
    lrc_content = pkg.get('lyric') if pkg else None

    if not lrc_content:
        return jsonify({'error': '未找到歌词'}), 404

    # 2. 清理文件名并保存 (与下载逻辑保持一致)
    safe_n, safe_a = re.sub(r'[\\/:*?"<>|]', '', str(name)), re.sub(r'[\\/:*?"<>|]', '', str(artist))
    fname = f"{safe_a} - {safe_n}"

    try:
        lrc_path = os.path.join(target_dir, f"{fname}.lrc")
        with open(lrc_path, 'w', encoding='utf-8') as f:
            f.write(lrc_content)
        return jsonify({'status': 'success', 'filename': f"{fname}.lrc", 'path': lrc_path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _handle_download_core(source, track_id, name, artist, album, pic_id, br, download_lyric, is_vip, subdir='', progress_callback=None):
    """极简线性下载流程：无分支判断，纯粹的数据处理"""
    def report(progress, status=None):
        if progress_callback:
            progress_callback(progress, status)

    try:
        target_dir = _resolve_download_dir(subdir)
    except ValueError as e:
        return {'error': str(e)}, 400

    report(2, 'queued')
    pkg = fetch_music_package(source, track_id, br, is_vip, pic_id, include_lyric=download_lyric)
    if not pkg or not pkg.get('url'):
        return {'error': '无法获取下载资源'}, 404
    report(5, 'downloading')

    # 清洗音频与封面
    cover_bytes = None
    if pkg.get('pic_url'):
        try:
            img_r = requests.get(pkg['pic_url'], headers=MEDIA_HEADERS, timeout=10)
            if img_r.status_code == 200: cover_bytes = img_r.content
        except: pass

    # 下载音频
    safe_n, safe_a = re.sub(r'[\\/:*?"<>|]', '', str(name)), re.sub(r'[\\/:*?"<>|]', '', str(artist))
    fname = f"{safe_a} - {safe_n}"
    try:
        with requests.get(pkg['url'], headers=MEDIA_HEADERS, stream=True, timeout=60) as r:
            if r.status_code != 200:
                return {'error': f'CDN 响应错误: {r.status_code}'}, r.status_code

            # 扩展名识别
            ctype = r.headers.get('content-type', '').lower()
            ext = mimetypes.guess_extension(ctype)
            if '.flac' in pkg['url'].lower() or 'audio/flac' in ctype: ext = '.flac'
            elif '.m4a' in pkg['url'].lower() or 'audio/mp4' in ctype: ext = '.m4a'
            elif not ext or ext == '.mpga': ext = '.mp3'

            fpath = os.path.join(target_dir, f"{fname}{ext}")
            try:
                total = int(r.headers.get('content-length') or 0)
            except ValueError:
                total = 0
            downloaded = 0
            fallback_progress = 5
            with open(fpath, 'wb') as f:
                for chunk in r.iter_content(8192):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        report(min(95, 5 + int(downloaded / total * 90)), 'downloading')
                    elif fallback_progress < 90:
                        fallback_progress += 1
                        report(fallback_progress, 'downloading')
    except Exception as e: return {'error': f'下载失败: {str(e)}'}, 500

    # 写入标签与歌词文件
    report(96, 'tagging')
    tag_ok = write_tags(fpath, ext, name, artist, album, cover_bytes, pkg.get('lyric') if download_lyric else None)
    res = {'status': 'success', 'filename': f"{fname}{ext}", 'bitrate': br, 'tags': 'ok' if tag_ok else 'err'}

    if download_lyric and pkg.get('lyric'):
        with open(os.path.join(target_dir, f"{fname}.lrc"), 'w', encoding='utf-8') as f:
            f.write(pkg['lyric'])
    report(100, 'success')
    return res, 200

def _cleanup_download_jobs():
    now = time.time()
    expired = [job_id for job_id, job in download_jobs.items() if now - job.get('created_at', now) > 1800]
    for job_id in expired:
        download_jobs.pop(job_id, None)


def _update_download_job(job_id, progress=None, status=None, **extra):
    job = download_jobs.get(job_id)
    if not job:
        return
    if progress is not None:
        job['progress'] = max(job.get('progress', 0), min(100, int(progress)))
    if status:
        job['status'] = status
    job.update(extra)


def _run_download_job(job_id, data):
    def progress_callback(progress, status=None):
        _update_download_job(job_id, progress, status)

    res, code = _handle_download_core(
        source=data.get('source'), track_id=data.get('id'), name=data.get('name'),
        artist=data.get('artist', 'Unknown'), album=data.get('album', ''),
        pic_id=data.get('pic_id', ''), br=data.get('br', 999),
        download_lyric=data.get('lyric', False), is_vip=data.get('vip', False),
        subdir=data.get('subdir', ''), progress_callback=progress_callback
    )
    if code == 200:
        _update_download_job(job_id, 100, 'success', filename=res.get('filename'), result=res)
    else:
        _update_download_job(job_id, None, 'error', error=res.get('error', '下载失败'), result=res)


@app.route('/api/download/start', methods=['POST'])
def start_download_job():
    _cleanup_download_jobs()
    data = request.json or {}
    job_id = hashlib.md5(f"{time.time()}-{data.get('id')}-{data.get('source')}".encode()).hexdigest()
    download_jobs[job_id] = {
        'status': 'queued',
        'progress': 0,
        'filename': '',
        'error': '',
        'created_at': time.time()
    }
    download_executor.submit(_run_download_job, job_id, data)
    return jsonify({'job_id': job_id})


@app.route('/api/download/progress/<job_id>')
def download_progress(job_id):
    job = download_jobs.get(job_id)
    if not job:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify({
        'status': job.get('status'),
        'progress': job.get('progress', 0),
        'filename': job.get('filename', ''),
        'error': job.get('error', ''),
        'result': job.get('result')
    })


@app.route('/api/download', methods=['POST'])
def download():
    data = request.json
    res, code = _handle_download_core(
        source=data.get('source'), track_id=data.get('id'), name=data.get('name'),
        artist=data.get('artist', 'Unknown'), album=data.get('album', ''),
        pic_id=data.get('pic_id', ''), br=data.get('br', 999),
        download_lyric=data.get('lyric', False), is_vip=data.get('vip', False),
        subdir=data.get('subdir', '')
    )
    return jsonify(res), code


def get_openapi_spec(is_vip=False):
    """
    定义 OpenAPI 3.0 规范，提供结构化排版。
    """
    if is_vip:
        source_desc = (
            "- `netease` (网易云 1)\n"
            "- `netease2` (网易云 2)\n"
            "- `tencent` (QQ音乐)\n"
            "- `kuwo` (酷我)\n"
            "- `tidal` (Tidal)\n"
            "- `qobuz` (Qobuz)\n"
            "- `joox` (JOOX)\n"
            "- `bilibili` (B站)\n"
            "- `apple` (苹果)\n"
            "- `ytmusic` (油管)\n"
            "- `spotify` (Spotify)"
        )
        br_desc = (
            "- `128 | standard` (标准音质)\n"
            "- `192` (较高音质)\n"
            "- `320 | exhigh` (极高音质)\n"
            "- `740 | lossless` (无损音质)\n"
            "- `999 | hires` (Hi-Res音质)\n"
            "- `jyeffect` (高清臻音)\n"
            "- `sky` (沉浸环绕声)\n"
            "- `jymaster` (超清母带)"
        )
        vip_param_desc = "VIP 模式"
    else:
        source_desc = (
            "- `netease` (网易云)\n"
            "- `kuwo` (酷我)\n"
            "- `joox` (JOOX)\n"
            "- `bilibili` (B站)"
        )
        br_desc = (
            "- `128 | standard` (标准音质)\n"
            "- `192` (较高音质)\n"
            "- `320 | exhigh` (极高音质)\n"
            "- `740 | lossless` (无损音质)\n"
            "- `999 | hires` (Hi-Res音质)"
        )
        vip_param_desc = "校验参数"

    spec = {
        "openapi": "3.0.0",
        "info": {
            "title": APP_NAME,
            "version": APP_VERSION,
            "description": "简约、高效的音乐搜索与下载接口。"
        },
        "paths": {
            "/api/search": {
                "get": {
                    "summary": "搜索",
                    "parameters": [
                        {"name": "name", "in": "query", "required": True, "description": "关键字", "schema": {"type": "string"}},
                        {"name": "source", "in": "query", "description": source_desc, "schema": {"type": "string", "default": "netease"}},
                        {"name": "pages", "in": "query", "description": "页码", "schema": {"type": "integer", "default": 1}}
                    ],
                    "responses": {"200": {"description": "成功"}}
                }
            },
            "/api/info": {
                "get": {
                    "summary": "详情",
                    "parameters": [
                        {"name": "id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "source", "in": "query", "required": True, "description": source_desc, "schema": {"type": "string"}},
                        {"name": "br", "in": "query", "description": br_desc, "schema": {"type": "string", "default": "999"}}
                    ],
                    "responses": {"200": {"description": "成功"}}
                }
            },
            "/api/preview": {
                "get": {
                    "summary": "试听",
                    "parameters": [
                        {"name": "id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "source", "in": "query", "required": True, "description": source_desc, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "返回 URL"}}
                }
            },
            "/api/cover": {
                "get": {
                    "summary": "封面",
                    "parameters": [
                        {"name": "id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "source", "in": "query", "required": True, "description": source_desc, "schema": {"type": "string"}}
                    ],
                    "responses": {"302": {"description": "重定向"}}
                }
            },
            "/api/download": {
                "post": {
                    "summary": "下载",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "成功"}}
                }
            },
            "/api/lyric": {
                "post": {
                    "summary": "歌词",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "成功"}}
                }
            },
            "/api/workflow": {
                "get": {
                    "summary": "自动化",
                    "parameters": [
                        {"name": "text", "in": "query", "required": True, "description": "分享链接/文本", "schema": {"type": "string"}},
                        {"name": "br", "in": "query", "description": br_desc, "schema": {"type": "string", "default": "999"}}
                    ],
                    "responses": {"200": {"description": "成功"}}
                }
            }
        }
    }

    if is_vip:
        spec["info"]["description"] = f"{APP_NAME} [VIP 完整版]"
        for path in ["/api/search", "/api/track_info", "/api/workflow"]:
            spec["paths"][path]["get"]["parameters"].append(
                {"name": "vip", "in": "query", "description": vip_param_desc, "schema": {"type": "string", "default": "1"}}
            )
        spec["paths"]["/api/download"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["vip"] = {
            "type": "boolean", "default": True, "description": vip_param_desc
        }
    return spec

@app.route('/openapi.json')
def openapi_spec_public():
    """公开版规范 JSON"""
    return jsonify(get_openapi_spec(is_vip=False))

@app.route('/vip/openapi.json')
def openapi_spec_vip():
    """VIP 版规范 JSON"""
    return jsonify(get_openapi_spec(is_vip=True))

@app.route('/api')
def api_docs_public():
    """公开版 API 文档"""
    return render_api_docs("/openapi.json", APP_NAME)

@app.route('/vip/api')
def api_docs_vip():
    """VIP 完整版 API 文档"""
    return render_api_docs("/vip/openapi.json", f"{APP_NAME} [VIP Mode]")
def render_api_docs(json_url, title):
    return f'''
    <!doctype html>
    <html>
      <head><title>{title}</title></head>
      <body>
        <script id="api-reference" data-url="{json_url}"></script>
        <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
      </body>
    </html>
    '''

@app.route('/api/info')
def track_info_endpoint():
    """详情路由：不再感知业务判断，直接调用物理隔离的分发"""
    pkg = fetch_music_package(
        request.args.get('source'), request.args.get('id'),
        request.args.get('br', '999'), request.args.get('vip') == '1'
    )
    return jsonify({'size': pkg.get('size', ''), 'level': pkg.get('level_text', '')}) if pkg else jsonify({'size': '', 'level': ''})


def get_music_info_for_workflow(source, track_id, is_vip, br='999'):
    """仅供 workflow 使用的元数据补全逻辑"""
    # 如果是网易云链接，不管识别出的 source 是啥，强制走 BugPk 获取完整信息
    if source in ['netease', 'netease2']:
        pkg = get_bugpk_handler(track_id, br)
        if pkg:
            return {
                'name': pkg.get('name'),
                'artist': pkg.get('artist', 'Unknown'),
                'album': pkg.get('album', ''),
                'pic_id': track_id
            }

    try:
        base, headers, _ = api_cfg(is_vip, source)
        params = {'types': 'search', 'source': source, 'name': track_id, 'count': 5}
        if is_vip: params['s'] = get_signature(track_id)
        resp = requests.get(base, params=params, headers=headers, timeout=5).json()
        if isinstance(resp, list):
            for item in resp:
                if str(item.get('id')) == str(track_id):
                    ar = item.get('artist', [])
                    return {
                        'name': item.get('name'),
                        'artist': ", ".join(ar) if isinstance(ar, list) else str(ar),
                        'album': item.get('album', ''),
                        'pic_id': item.get('pic_id', item.get('id', ''))
                    }
    except: pass
    return None

@app.route('/api/workflow')
def workflow_endpoint():
    """解析 Workflow 链接并下载"""
    text = request.args.get('text', '')
    br = request.args.get('br', '999')
    is_vip = request.args.get('vip') == '1'
    subdir = request.args.get('subdir', '')

    url = extract_url(text)
    if not url:
        return jsonify({'error': '未能识别出链接'}), 400
    
    # ── 1. 提取标识 (只识别，不修改全局 source) ──────────────────────
    source, song_id = parse_music_link(text)
    if not source or not song_id:
        return jsonify({'error': '未能解析链接 ID，请确保平台支持'}), 400
    
    # ── 2. workflow 特殊逻辑：网易云强制使用 Netease2 (BugPk) ───────
    effective_source = 'netease2' if source == 'netease' else source
    
    # ── 3. 补全元数据 (利用缓存：get_bugpk_handler) ─────────────────
    info = get_music_info_for_workflow(effective_source, song_id, is_vip, br)
    if not info:
        info = {'name': song_id, 'artist': 'Unknown', 'album': '', 'pic_id': song_id}
        
    # ── 4. 走内部核心下载逻辑 ───────────────────────────────────────
    # 注意：_handle_download_core 会再次调用 fetch_music_package -> get_bugpk_handler
    # 因为有 lru_cache，第二次调用将直接命中缓存，不会请求 BugPk API
    res, code = _handle_download_core(
        source=effective_source,
        track_id=song_id,
        name=info.get('name'),
        artist=info.get('artist'),
        album=info.get('album'),
        pic_id=info.get('pic_id'),
        br=br,
        download_lyric=False,
        is_vip=is_vip,
        subdir=subdir
    )
    return jsonify(res), code


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
