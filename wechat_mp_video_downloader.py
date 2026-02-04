import argparse
import html as htmllib
import os
import re
import sys
from typing import List, Dict, Optional

import requests

UA_WECHAT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.0"
)


def fetch_article_html(url: str, cookie: Optional[str]) -> str:
    headers = {"User-Agent": UA_WECHAT}
    if cookie:
        headers["Cookie"] = cookie
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    # If WeChat blocks, it often redirects to wappoc_appmsgcaptcha
    blocked_markers = [
        "wappoc_appmsgcaptcha",
        "\u73af\u5883\u5f02\u5e38",
        "\u53bb\u9a8c\u8bc1",
    ]
    if "wappoc_appmsgcaptcha" in resp.url or any(m in resp.text for m in blocked_markers):
        raise RuntimeError(
            "WeChat blocked this request (environment/captcha). "
            "Open the URL in WeChat or a mobile browser once, then retry. "
            "If it still fails, pass cookies with --cookie."
        )
    resp.raise_for_status()
    return resp.text


def extract_var(html_text: str, name: str) -> Optional[str]:
    m = re.search(rf'var\s+{name}\s*=\s*"(.*?)"', html_text)
    return m.group(1) if m else None


def extract_video_page_infos(html_text: str) -> List[str]:
    idx = html_text.find("videoPageInfos")
    if idx == -1:
        return []
    start = html_text.find("[", idx)
    if start == -1:
        return []

    level = 0
    in_str = False
    str_char = ""
    esc = False
    end = None
    for i in range(start, len(html_text)):
        ch = html_text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == str_char:
                in_str = False
        else:
            if ch in ("\"", "'"):
                in_str = True
                str_char = ch
            elif ch == "[":
                level += 1
            elif ch == "]":
                level -= 1
                if level == 0:
                    end = i
                    break
    if end is None:
        return []

    arr_text = html_text[start + 1 : end]

    objs = []
    start = None
    level = 0
    in_str = False
    str_char = ""
    esc = False
    for i, ch in enumerate(arr_text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == str_char:
                in_str = False
        else:
            if ch in ("\"", "'"):
                in_str = True
                str_char = ch
            elif ch == "{":
                if level == 0:
                    start = i
                level += 1
            elif ch == "}":
                level -= 1
                if level == 0 and start is not None:
                    objs.append(arr_text[start : i + 1])
                    start = None
    return objs


def parse_video_entries(obj_text: str) -> Optional[Dict]:
    vid_m = re.search(r"video_id:\s*'([^']+)'", obj_text)
    if not vid_m:
        return None
    vid = vid_m.group(1)

    mt_m = re.search(r"mp_video_trans_info\s*:\s*\[(.*?)\]", obj_text, flags=re.S)
    if not mt_m:
        return None
    mt = mt_m.group(1)

    entries = []
    for ent in re.finditer(r"\{(.*?)\}", mt, flags=re.S):
        block = ent.group(1)
        fs = re.search(r"filesize:\s*'?(\d+)'?", block)
        fmt = re.search(r"format_id:\s*'?(\d+)'?", block)
        url = re.search(r"url:\s*\('([^']+)'\)", block)
        if fs and url:
            url_raw = url.group(1)
            url_clean = url_raw.replace("\\x26amp;", "&")
            url_clean = htmllib.unescape(url_clean)
            if url_clean.startswith("http://"):
                url_clean = "https://" + url_clean[len("http://") :]
            entries.append(
                {
                    "filesize": int(fs.group(1)),
                    "format_id": int(fmt.group(1)) if fmt else None,
                    "url": url_clean,
                }
            )

    if not entries:
        return None

    return {"vid": vid, "entries": entries}


def extract_mpvids(html_text: str) -> List[str]:
    mpvids = re.findall(r"data-mpvid=\\x22(wxv_[0-9a-zA-Z]+)\\x22", html_text)
    if not mpvids:
        mpvids = re.findall(r'data-mpvid="(wxv_[0-9a-zA-Z]+)"', html_text)
    # unique preserve order
    seen = set()
    out = []
    for v in mpvids:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def extract_mp4_groups_from_html(html_text: str) -> List[Dict]:
    # Fallback: parse mp4 URLs directly from HTML
    urls = re.findall(r"https?://mpvideo\.qpic\.cn/[^\"'\s]+?\.mp4[^\"'\s]*", html_text)
    if not urls:
        return []
    seen_url = set()
    groups = {}
    order = []
    for raw in urls:
        if raw in seen_url:
            continue
        seen_url.add(raw)
        url_clean = raw.replace("\\x26amp;", "&")
        url_clean = htmllib.unescape(url_clean)
        if url_clean.startswith("http://"):
            url_clean = "https://" + url_clean[len("http://") :]
        m = re.search(r"mpvideo\.qpic\.cn/([^\.]+)\.f(\d+)\.mp4", url_clean)
        if not m:
            continue
        base = m.group(1)
        fmt = int(m.group(2))
        if base not in groups:
            groups[base] = []
            order.append(base)
        groups[base].append({"filesize": None, "format_id": fmt, "url": url_clean})
    return [{"vid": base, "entries": groups[base]} for base in order]


def get_video_title(vid: str, article_url: str, biz: str, mid: str, idx: str, sn: str) -> Optional[str]:
    api = (
        "https://mp.weixin.qq.com/mp/videoplayer"
        f"?action=get_mp_video_play_url&vid={vid}&__biz={biz}&mid={mid}&idx={idx}&sn={sn}"
    )
    headers = {"User-Agent": UA_WECHAT, "Referer": article_url}
    try:
        r = requests.get(api, headers=headers, timeout=20)
        data = r.json()
        return data.get("video_title")
    except Exception:
        return None


def pick_entry(entries: List[Dict], quality: str) -> Dict:
    if quality.isdigit():
        fmt = int(quality)
        for e in entries:
            if e.get("format_id") == fmt:
                return e
    if quality == "smallest":
        return min(entries, key=lambda e: e.get("filesize") or 0)
    # default: largest
    if all(e.get("filesize") is not None for e in entries):
        return max(entries, key=lambda e: e["filesize"])
    # fallback: pick highest format_id
    return max(entries, key=lambda e: e.get("format_id") or 0)


def sanitize_filename(name: str) -> str:
    # Keep unicode letters/numbers and common separators
    # Replace other characters with underscore
    return re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", name).strip("_")


def download_file(url: str, out_path: str, referer: str) -> None:
    headers = {"User-Agent": UA_WECHAT, "Referer": referer}
    with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download WeChat article MP4 videos.")
    parser.add_argument("url", help="WeChat article URL")
    parser.add_argument(
        "--outdir",
        default=r"C:\\WeChatVideos",
        help="Output directory (default: C:\\WeChatVideos)",
    )
    parser.add_argument(
        "--quality",
        default="largest",
        help="largest | smallest | format_id (e.g. 10002)",
    )
    parser.add_argument(
        "--cookie",
        default="",
        help="Optional Cookie header if WeChat blocks access",
    )
    parser.add_argument(
        "--dump-html",
        default="",
        help="Optional path to save raw HTML for debugging",
    )

    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    html_text = fetch_article_html(args.url, args.cookie)
    if args.dump_html:
        with open(args.dump_html, "w", encoding="utf-8") as f:
            f.write(html_text)
    biz = extract_var(html_text, "biz") or ""
    mid = extract_var(html_text, "mid") or ""
    idx = extract_var(html_text, "idx") or ""
    sn = extract_var(html_text, "sn") or ""

    videos = []
    objs = extract_video_page_infos(html_text)
    if objs:
        for obj in objs:
            info = parse_video_entries(obj)
            if info:
                videos.append(info)

    if not videos:
        # fallback: parse mp4 urls directly
        videos = extract_mp4_groups_from_html(html_text)
        if not videos:
            print(
                "No videoPageInfos or mp4 URLs found. Possible reasons: "
                "WeChat blocked the request, the article has no MP videos, "
                "or the page requires login. Try again with --cookie or --dump-html."
            )
            return 2

    for i, info in enumerate(videos, 1):
        entry = pick_entry(info["entries"], args.quality)
        title = None
        if biz and mid and idx and sn and info["vid"].startswith("wxv_"):
            title = get_video_title(info["vid"], args.url, biz, mid, idx, sn)
        if not title:
            title = info["vid"]

        safe = sanitize_filename(title) or info["vid"]
        filename = f"{i:02d}_{safe}.mp4"
        out_path = os.path.join(args.outdir, filename)

        if os.path.exists(out_path):
            if entry.get("filesize") is None:
                if os.path.getsize(out_path) > 0:
                    print(f"SKIP {filename} (already exists)")
                    continue
            else:
                if os.path.getsize(out_path) >= entry["filesize"] * 0.95:
                    print(f"SKIP {filename} (already exists)")
                    continue

        print(f"DOWNLOADING {filename} ...")
        download_file(entry["url"], out_path, args.url)
        print(f"OK {filename}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
