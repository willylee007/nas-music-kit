import os
import requests
import mimetypes
import urllib.parse
import hashlib
import time
import re
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, redirect, send_from_directory

from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, USLT, ID3NoHeaderError
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover

app = Flask(__name__, template_folder='.', static_folder='static', static_url_path='/static')

# 下载目录：Docker 默认 /music，本地测试用环境变量覆盖
DOWNLOAD_DIR = os.environ.get('DOWNLOAD_DIR', '/music')
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
    'User-Agent': API_HEADERS['User-Agent']
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
        # 使用自定义 UA 避免被风控
        resp = requests.head(url, headers=API_HEADERS, allow_redirects=True, timeout=5)
        return resp.url
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
    if 'music.163.com' in url or '163.com' in url:
        m = re.search(r'id=(\d+)', url)
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

def get_extra_info(is_vip, source, track_id, br='999'):
    """获取单首歌曲的额外信息（大小和音质）"""
    base, headers, is_bugpk = api_cfg(is_vip, source)
    try:
        if is_bugpk:
            bugpk_levels = ['standard', 'exhigh', 'lossless', 'hires', 'jyeffect', 'sky', 'jymaster']
            
            # 如果传入的本身就是合法的 BugPk level 字符串，则直接使用
            if br in bugpk_levels:
                level = br
            else:
                # 否则进行数值映射
                try: bitrate = int(br)
                except: bitrate = 999
                
                if bitrate == 999:  level = 'hires'
                elif bitrate == 740:  level = 'lossless'
                elif bitrate == 320:  level = 'exhigh'
                else: level = 'standard' # 192 和 128 默认使用 standard
            
            params = {'type': 'json', 'ids': track_id, 'level': level}
            resp = requests.get(base, params=params, headers=headers, timeout=3)
            data = resp.json()
            if data.get('code') == 200 or data.get('status') == 200:
                d = data.get('data', data)
                size_bytes = d.get('size', 0)
                # 统一转换体积为 MB
                try:
                    size_mb = f"{int(size_bytes) / (1024*1024):.2f}MB" if size_bytes else ""
                except:
                    size_mb = str(size_bytes) if size_bytes else ""
                
                # 统一映射 BugPk level 为中文描述
                level_raw = d.get('level', '')
                level_map = {
                    'standard': '标准音质',
                    '192': '较高音质',
                    'exhigh': '极高音质',
                    'lossless': '无损音质',
                    'hires': 'Hi-Res音质',
                    'jyeffect': '高清臻音',
                    'sky': '沉浸环绕声',
                    'jymaster': '超清母带'
                }
                level = level_map.get(level_raw, level_raw)

                return {
                    'size': size_mb,
                    'level': level
                }
        else:
            params = {'types': 'url', 'source': source, 'id': track_id, 'br': br}
            if is_vip: params['s'] = get_signature(track_id)
            resp = requests.get(base, params=params, headers=headers, timeout=3)
            data = resp.json()
            if data and data.get('url'):
                size_bytes = data.get('size', 0)
                size_mb = f"{size_bytes / (1024*1024):.2f}MB" if size_bytes else ""
                br_val = int(data.get('br', 0))
                # 统一显示中文
                if br == 'jymaster' or data.get('level') == 'jymaster': level = "超清母带"
                elif br_val == 999: level = "Hi-Res音质"
                elif br_val == 740: level = "无损音质"
                elif br_val == 320: level = "极高音质"
                elif br_val == 192: level = "较高音质"
                else: level = "标准音质"
                return {'size': size_mb, 'level': level}
    except:
        pass
    return {'size': '', 'level': ''}
def get_song_metadata_internal(is_vip, source, track_id):
    """内部元数据抓取：用于在下载任务缺少信息时自动补全。"""
    try:
        if source in ['netease', 'netease2']:
            resp = requests.get(API_BUGPK, params={'type': 'song', 'id': track_id}, timeout=3)
            data = resp.json()
            if data.get('code') == 200:
                d = data.get('data', {})
                return {'name': d.get('name', ''), 'artist': d.get('singer', ''), 'album': d.get('album', ''), 'pic_id': d.get('picimg', '')}
        
        base, headers, _ = api_cfg(is_vip, source)
        search_params = {'types': 'search', 'source': source, 'name': track_id, 'count': 1}
        if is_vip: search_params['s'] = get_signature(track_id)
        resp = requests.get(base, params=search_params, headers=headers, timeout=3)
        results = resp.json()
        if isinstance(results, list) and len(results) > 0:
            item = results[0]
            if str(item.get('id')) == str(track_id):
                artist_val = item.get('artist', [])
                artist_name = ", ".join(artist_val) if isinstance(artist_val, list) else str(artist_val)
                return {'name': item.get('name', ''), 'artist': artist_name, 'album': item.get('album', ''), 'pic_id': item.get('pic_id', '')}
    except: pass
    return None



# ── 工具函数 ───────────────────────────────────────────────────────────────

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
            resp = requests.get(base, params={'type': 'song', 'id': pic_id}, headers=headers, timeout=8)
            data = resp.json()
            if data.get('code') == 200:
                d = data.get('data', {})
                img_url = d.get('picimg') or d.get('picUrl') or d.get('pic')
        
        # 2. 其他平台或 BugPk 降级
        if not img_url:
            params = {'types': 'pic', 'source': source, 'id': pic_id, 'size': 300}
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
    is_vip  = request.args.get('vip') == '1'

    if not keyword:
        return jsonify({'error': 'Keyword is required'}), 400

    base, headers, is_bugpk = api_cfg(is_vip, source)
    try:
        # 强制转换为整数，默认 20
        try:
            count = int(request.args.get('count', 20))
        except:
            count = 20
        
        try:
            page = int(page)
        except:
            page = 1
            
        if is_bugpk:
            params = {
                'type': 'search',
                'keywords': keyword,
                'limit': count,
                'offset': (page - 1) * count
            }
        else:
            params = {
                'types': 'search',
                'source': source,
                'name': keyword,
                'count': count,
                'pages': page
            }
            # 接口校验
            if is_vip:
                params['s'] = get_signature(keyword)
            
        resp = requests.get(base, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        
        # 结果格式标准化
        results = []
        if is_bugpk:
            if isinstance(data, dict) and data.get('code') == 200:
                raw_songs = data.get('data', {}).get('songs', [])
                for s in raw_songs:
                    results.append({
                        'id': s.get('id'),
                        'name': s.get('name'),
                        # Normalize artists string to list
                        'artist': [s.get('artists')] if isinstance(s.get('artists'), str) else s.get('artists', []),
                        'album': s.get('album', ''),
                        'pic_id': s.get('id'),
                        'source': source
                    })
        else:
            if isinstance(data, list):
                results = data[:count]
        
        # 补充 source 信息以便前端处理链接
        for item in results:
            if 'source' not in item:
                item['source'] = source

        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/preview')
def preview():
    """返回试听链接，128k 节省流量"""
    source   = request.args.get('source')
    track_id = request.args.get('id')
    br       = request.args.get('br', '128')
    is_vip   = request.args.get('vip') == '1'

    if not source or not track_id:
        return jsonify({'error': 'Missing required fields'}), 400

    base, headers, is_bugpk = api_cfg(is_vip, source)
    try:
        if is_bugpk:
            # BugPk 音质映射
            level = 'standard'
            if br == '320': level = 'exhigh'
            elif br in ['740', '999', '2000']: level = 'lossless'
            params = {'type': 'url', 'id': track_id, 'level': level}
        else:
            params = {
                'types': 'url',
                'source': source,
                'id': track_id,
                'br': br
            }
            if is_vip:
                params['s'] = get_signature(track_id)
            
        resp = requests.get(base, params=params, headers=headers, timeout=10)
        data = resp.json()
        
        real_url = ""
        if is_bugpk:
            if data.get('code') == 200 and isinstance(data.get('data'), list) and len(data['data']) > 0:
                real_url = normalize_url(data['data'][0].get('url'), source)
        else:
            if data and data.get('url'):
                real_url = normalize_url(data['url'], source)
                
        if real_url:
            return jsonify({'url': real_url})
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

    safe_name   = "".join([c for c in name   if c.isalpha() or c.isdigit() or c in ' .-_()']).strip()
    safe_artist = "".join([c for c in artist if c.isalpha() or c.isdigit() or c in ' .-_()']).strip()
    base_filename = f"{safe_artist} - {safe_name}"

    base, headers, is_bugpk = api_cfg(is_vip, source)
    try:
        if is_bugpk:
            params = {'type': 'lyric', 'id': track_id}
        else:
            params = {
                'types': 'lyric',
                'source': source,
                'id': track_id
            }
            if is_vip:
                params['s'] = get_signature(track_id)
            
        lyric_resp = requests.get(base, params=params, headers=headers, timeout=10)
        lyric_resp.raise_for_status()
        lyric_raw = lyric_resp.json()
 
        lrc_content = ""
        if is_bugpk:
            if lyric_raw.get('code') == 200:
                d = lyric_raw.get('data', {})
                lrc_content = d.get('lrc', '')
                tlyric = d.get('tlyric', '')
                if tlyric: lrc_content += '\n\n' + tlyric
        else:
            lrc_content = lyric_raw.get('lyric', '')
            tlyric = lyric_raw.get('tlyric', '')
            if tlyric: lrc_content += '\n\n' + tlyric
            
        if not lrc_content:
            return jsonify({'error': '未找到歌词'}), 404

        lrc_path = os.path.join(DOWNLOAD_DIR, f"{base_filename}.lrc")
        with open(lrc_path, 'w', encoding='utf-8') as f:
            f.write(lrc_content)

        return jsonify({'status': 'success', 'filename': f"{base_filename}.lrc", 'path': lrc_path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _handle_download_core(source, track_id, name=None, artist='Unknown', album='', pic_id='', br=999, download_lyric=True, is_vip=False):
    """
    核心下载：支持元数据补全、Failover 冗余与码率降级。
    """
    # 1. 元数据自愈
    if not name or name == str(track_id) or artist == 'Unknown':
        meta = get_song_metadata_internal(is_vip, source, track_id)
        if meta:
            name, artist, album, pic_id = meta.get('name') or name, meta.get('artist') or artist, meta.get('album') or album, meta.get('pic_id') or pic_id

    # 兜底：确保 name 和 artist 不是 None，防止后续清洗报错
    name = str(name or track_id)
    artist = str(artist or 'Unknown')
    album = str(album or '')

    # 2. 接口 Failover 链条设置
    if source in ['netease', 'netease2']:
        chain = [{'sid': 'netease2', 'base': API_BUGPK, 'is_bugpk': True}, {'sid': 'netease', 'base': (API_VIP if is_vip else API_PUBLIC), 'is_bugpk': False}]
    else:
        base, headers, is_bugpk = api_cfg(is_vip, source)
        chain = [{'sid': source, 'base': base, 'is_bugpk': is_bugpk}]

    available_fqs = [999, 740, 320, 192, 128]
    bugpk_levels = ['jymaster', 'sky', 'jyeffect', 'hires', 'lossless', 'exhigh', 'standard']
    
    download_url = None
    final_br, final_sid, final_is_bugpk, final_headers = 0, source, False, {**API_HEADERS}

    for probe in chain:
        sid, base, is_bugpk = probe['sid'], probe['base'], probe['is_bugpk']
        headers = {} if is_bugpk else {**API_HEADERS}
        
        if is_bugpk:
            try:
                requested_num = int(br or 999)
                if requested_num >= 999: target_lv = 'hires' 
                elif requested_num >= 740: target_lv = 'lossless'
                elif requested_num >= 320: target_lv = 'exhigh'
                else: target_lv = 'standard'
            except:
                target_lv = str(br) if str(br) in bugpk_levels else 'hires'
            
            try: s_list = bugpk_levels[bugpk_levels.index(target_lv):]
            except: s_list = bugpk_levels
        else:
            try: requested_num = int(br or 999)
            except: requested_num = 999
            try: s_list = available_fqs[available_fqs.index(requested_num):] if requested_num in available_fqs else available_fqs
            except: s_list = available_fqs

        for br_val in s_list:
            try:
                if is_bugpk:
                    lv = br_val if isinstance(br_val, str) else ('hires' if br_val >= 999 else ('lossless' if br_val >= 740 else ('exhigh' if br_val >= 320 else 'standard')))
                    p = {'type': 'url', 'id': track_id, 'level': lv}
                else:
                    p = {'types': 'url', 'source': sid, 'id': track_id, 'br': br_val}
                    if is_vip: p['s'] = get_signature(track_id)
                
                u_resp = requests.get(base, params=p, headers=headers, timeout=10)
                u_data = u_resp.json()
                u_url = ""
                if is_bugpk:
                    if u_data.get('code') == 200:
                        d = u_data.get('data', [])
                        u_url = d[0].get('url', '') if isinstance(d, list) and len(d) > 0 else (d.get('url', '') if isinstance(d, dict) else '')
                else: u_url = u_data.get('url', '')

                if u_url:
                    download_url, final_br, final_sid, final_is_bugpk, final_headers = normalize_url(u_url, sid), br_val, sid, is_bugpk, headers
                    break
            except: continue
        if download_url: break

    if not download_url: return {'error': '无法获取下载链接（可能版权限制或需要VIP）'}, 404

    # 封面与歌词
    cover_bytes = None
    if pic_id:
        try:
            if final_is_bugpk:
                p_data = requests.get(API_BUGPK, params={'type': 'song', 'id': track_id}, timeout=8).json()
                pic_url = p_data.get('data', {}).get('picimg', '') if p_data.get('code') == 200 else ''
            else:
                p_p = {'types': 'pic', 'source': final_sid, 'id': pic_id, 'size': 300}
                if is_vip: p_p['s'] = get_signature(pic_id)
                pic_url = requests.get(base, params=p_p, headers=final_headers, timeout=8).json().get('url', '')
            if pic_url:
                img_r = requests.get(pic_url, timeout=10)
                if img_r.status_code == 200: cover_bytes = img_r.content
        except: pass

    lyric_text = None
    try:
        l_p = {'type': 'lyric', 'id': track_id} if final_is_bugpk else {'types': 'lyric', 'source': final_sid, 'id': track_id}
        if not final_is_bugpk and is_vip: l_p['s'] = get_signature(track_id)
        l_d = requests.get(base, params=l_p, headers=final_headers, timeout=10).json()
        raw_lrc = (l_d.get('data', {}).get('lrc', '') if final_is_bugpk else l_d.get('lyric', ''))
        t_lrc = (l_d.get('data', {}).get('tlyric', '') if final_is_bugpk else l_d.get('tlyric', ''))
        if raw_lrc: lyric_text = raw_lrc + ('\n\n' + t_lrc if t_lrc else '')
    except: pass

    # 文件名清洗 (确保无空值)
    safe_name = "".join([c for c in (name or str(track_id)) if c.isalnum() or c in ' .-_()']).strip()
    safe_artist = "".join([c for c in (artist or 'Unknown') if c.isalnum() or c in ' .-_()']).strip()
    base_filename = f"{safe_artist} - {safe_name}"
    
    try:
        with requests.get(download_url, headers=MEDIA_HEADERS, timeout=60, stream=True) as r:
            r.raise_for_status()
            ext = mimetypes.guess_extension(r.headers.get('content-type', '')) or ('.flac' if '.flac' in download_url else ('.m4a' if '.m4a' in download_url else '.mp3'))
            ext = {'.mpga': '.mp3', '.mp4a': '.m4a', '.adts': '.aac'}.get(ext, ext)
            filename = f"{base_filename}{ext}"
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
    except Exception as e: return {'error': f"下载失败: {e}"}, 500

    tag_ok = write_tags(filepath, ext, name, artist, album, cover_bytes, lyric_text)
    res = {'status': 'success', 'filename': filename, 'path': filepath, 'bitrate': final_br, 'tags': 'ok' if tag_ok else 'skipped'}
    if download_lyric and lyric_text:
        lrc_p = os.path.join(DOWNLOAD_DIR, f"{base_filename}.lrc")
        with open(lrc_p, 'w', encoding='utf-8') as f: f.write(lyric_text)
        res['lyric_file'] = f"{base_filename}.lrc"
    return res, 200

@app.route('/api/download', methods=['POST'])
def download():
    data = request.json
    res, code = _handle_download_core(
        source=data.get('source'), track_id=data.get('id'), name=data.get('name'),
        artist=data.get('artist', 'Unknown'), album=data.get('album', ''),
        pic_id=data.get('pic_id', ''), bitrate=data.get('br', 999),
        download_lyric=data.get('lyric', False), is_vip=data.get('vip', False)
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
    """按需获取单曲的额外信息（大小和音质）"""
    source   = request.args.get('source')
    track_id = request.args.get('id')
    br       = request.args.get('br', '999') # 获取请求的码率
    is_vip   = request.args.get('vip') == '1'
    
    if not source or not track_id:
        return jsonify({'error': 'Missing source or id'}), 400
        
    info = get_extra_info(is_vip, source, track_id, br)
    return jsonify(info)


@app.route('/api/workflow')
def workflow_endpoint():
    """解析 Workflow 链接并下载"""
    text = request.args.get('text', '')
    br = request.args.get('br', '999')
    is_vip = request.args.get('vip') == '1'
    
    source, song_id = parse_music_link(text)
    if not source or not song_id:
        return jsonify({'error': '未能识别该链接，请确保包含有效的音乐分享地址'}), 400
    
    # 直接调用内部核心下载逻辑，无需发送 HTTP 请求
    res, code = _handle_download_core(
        source=source,
        track_id=song_id,
        br=br,
        download_lyric=True,
        is_vip=is_vip
    )
    return jsonify(res), code


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
