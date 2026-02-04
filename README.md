# wechatdownloader

Download MP4 videos from WeChat official account articles.

## Requirements

- Python 3.8+
- requests

Install dependency:

```
pip install requests
```

## Usage

```
python wechat_mp_video_downloader.py "<WeChat article URL>"
```

Options:

- `--outdir` Output directory (default: `C:\WeChatVideos`)
- `--quality` `largest` | `smallest` | format_id (e.g. `10002`)
- `--cookie` Optional Cookie header when access is blocked
- `--dump-html` Save raw HTML for debugging

Example:

```
python wechat_mp_video_downloader.py "https://mp.weixin.qq.com/s/xxxxxxxx" --quality largest --outdir C:\WeChatVideos
```

## Notes

If WeChat blocks access (environment/captcha), open the URL once in WeChat or a mobile browser, then retry. If it still fails, pass cookies with `--cookie`.
