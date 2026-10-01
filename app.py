import json
import sqlite3
import urllib.parse
import urllib.request
import html
import re
import base64
import struct
import os

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


DB = Path(__file__).with_name("radar.db")


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS videos(
            post_id TEXT PRIMARY KEY,
            user_id TEXT,
            username TEXT,
            caption TEXT,
            hashtags TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            product TEXT,
            video_url TEXT,
            shopee_url TEXT,
            source TEXT
        )
        """
    )

    return c


def token(post_id, user_id):
    try:
        return base64.b64encode(
            struct.pack("<QQ", int(post_id), int(user_id))
        ).decode()
    except Exception:
        return ""


def walk(x, out):
    if isinstance(x, dict):

        meta = x.get("meta") if isinstance(x.get("meta"), dict) else x

        pid = meta.get("postId") or meta.get("post_id")
        uid = meta.get("userId") or meta.get("user_id")

        cnt = (
            meta.get("countInfo")
            or meta.get("count_info")
            or {}
        )

        if pid:

            content = (
                x.get("content")
                if isinstance(x.get("content"), dict)
                else x
            )

            cap = (
                content.get("caption")
                or meta.get("caption")
                or ""
            )

            hs = re.findall(r"#([\wÀ-ÿ]+)", cap)

            user = (
                meta.get("userName")
                or meta.get("username")
                or ""
            )

            vid = (
                content.get("video")
                if isinstance(content.get("video"), dict)
                else {}
            )

            vurl = (
                vid.get("url")
                or vid.get("videoUrl")
                or ""
            )

            t = token(pid, uid) if uid else ""

            surl = (
                "https://sv.shopee.com.br/share-video/"
                + urllib.parse.quote(t, safe="")
                if t
                else ""
            )

            out[str(pid)] = {
                "post_id": str(pid),
                "user_id": str(uid or ""),
                "username": user,
                "caption": cap,
                "hashtags": ",".join(hs),
                "views": cnt.get("views")
                or cnt.get("view_count")
                or 0,
                "likes": cnt.get("likes")
                or cnt.get("like_cnt")
                or 0,
                "comments": cnt.get("comments")
                or cnt.get("comment_cnt")
                or 0,
                "product": "",
                "video_url": vurl,
                "shopee_url": surl,
                "source": "hashtag",
            }

        for v in x.values():
            walk(v, out)

    elif isinstance(x, list):
        for v in x:
            walk(v, out)


def collect(tag):

    tag = tag.strip().lstrip("#")

    if not tag:
        return 0, "Hashtag vazia"

    url = (
        "https://sv.shopee.com.br/web/hashtag/"
        + urllib.parse.quote(tag)
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 15) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0 Mobile Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        },
    )

    raw = (
        urllib.request.urlopen(req, timeout=25)
        .read()
        .decode("utf-8", "ignore")
    )

    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>'
        r"(.*?)"
        r"</script>",
        raw,
        re.S,
    )

    if not m:
        return 0, "NEXT_DATA não encontrado"

    data = json.loads(html.unescape(m.group(1)))

    out = {}

    walk(data, out)

    c = db()

    for r in out.values():

        c.execute(
            """
            INSERT INTO videos VALUES(
                :post_id,
                :user_id,
                :username,
                :caption,
                :hashtags,
                :views,
                :likes,
                :comments,
                :product,
                :video_url,
                :shopee_url,
                :source
            )

            ON CONFLICT(post_id) DO UPDATE SET

                user_id=excluded.user_id,
                username=excluded.username,
                caption=excluded.caption,
                hashtags=excluded.hashtags,
                views=excluded.views,
                likes=excluded.likes,
                comments=excluded.comments,
                video_url=excluded.video_url,
                shopee_url=excluded.shopee_url
            """,
            r,
        )

    c.commit()
    c.close()

    return len(out), url


PAGE = """<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>Radar Shopee Vídeo</title>

<style>

*{
    box-sizing:border-box;
}

body{
    font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    background:#f6f7f9;
    margin:0;
    color:#171717;
}

.wrap{
    max-width:1100px;
    margin:auto;
    padding:24px;
}

.hero{
    background:#ee4d2d;
    color:white;
    padding:26px;
    border-radius:20px;
}

.hero h1{
    margin-top:0;
}

form{
    display:flex;
    gap:8px;
    margin-top:16px;
}

input,
select,
button{
    padding:13px;
    border:0;
    border-radius:12px;
    font-size:16px;
}

input{
    flex:1;
    min-width:0;
}

button{
    background:#111;
    color:white;
    cursor:pointer;
}

.bar{
    display:flex;
    gap:10px;
    margin:18px 0;
    align-items:center;
    flex-wrap:wrap;
}

.grid{
    display:grid;
    grid-template-columns:
        repeat(auto-fill,minmax(250px,1fr));
    gap:14px;
}

.card{
    background:white;
    border-radius:16px;
    padding:16px;
    box-shadow:0 2px 12px #0000000b;
}

.u{
    font-weight:700;
}

.cap{
    min-height:63px;
    max-height:90px;
    overflow:hidden;
    color:#444;
}

.stats{
    display:flex;
    gap:12px;
    font-weight:700;
    margin:12px 0;
    flex-wrap:wrap;
}

.tag{
    font-size:12px;
    color:#ee4d2d;
    word-break:break-word;
}

.open{
    display:inline-block;
    text-decoration:none;
    background:#ee4d2d;
    color:white;
    padding:9px 12px;
    border-radius:10px;
}

.muted{
    color:#666;
}

@media(max-width:600px){

    .wrap{
        padding:12px;
    }

    .hero{
        padding:20px;
    }

    form{
        flex-direction:column;
    }

}

</style>

</head>

<body>

<div class="wrap">

<div class="hero">

<h1>Radar Shopee Vídeo 🇧🇷</h1>

<div>
Descubra e organize vídeos públicos por nicho/hashtag.
</div>

<form method="post" action="/collect">

<input
name="tag"
placeholder="Ex.: gokoco, maquiagem, cozinha"
required
>

<button type="submit">
Coletar
</button>

</form>

</div>

<div class="bar">

<b>{{COUNT}} vídeos no banco</b>

<form
method="get"
action="/"
style="margin:0"
>

<input
name="q"
value="{{Q}}"
placeholder="Buscar legenda, hashtag ou criador"
>

<select name="sort">

<option value="views">
Mais vistos
</option>

<option value="likes">
Mais curtidos
</option>

<option value="comments">
Mais comentados
</option>

</select>

<button type="submit">
Buscar
</button>

</form>

</div>

<div class="grid">

{{CARDS}}

</div>

</div>

</body>

</html>
"""


class H(BaseHTTPRequestHandler):

    def send(self, s, code=200, typ="text/html; charset=utf-8"):

        self.send_response(code)

        self.send_header("Content-Type", typ)

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.end_headers()

        self.wfile.write(s.encode("utf-8"))

    def do_POST(self):

        if self.path != "/collect":

            self.send("Não encontrado", 404)

            return

        n = int(
            self.headers.get(
                "Content-Length",
                "0"
            )
        )

        body = self.rfile.read(n).decode(
            "utf-8",
            "ignore"
        )

        d = urllib.parse.parse_qs(body)

        tag = d.get("tag", [""])[0]

        try:

            collect(tag)

            self.send_response(303)

            self.send_header(
                "Location",
                "/?q="
                + urllib.parse.quote(tag)
            )

            self.end_headers()

        except Exception as e:

            self.send(
                "<h2>Erro na coleta</h2>"
                "<pre>"
                + html.escape(str(e))
                + "</pre>",
                500,
            )

    def do_GET(self):

        u = urllib.parse.urlparse(
            self.path
        )

        if u.path == "/health":

            self.send(
                "OK",
                200,
                "text/plain; charset=utf-8",
            )

            return

        if u.path != "/":

            self.send(
                "Não encontrado",
                404,
            )

            return

        qs = urllib.parse.parse_qs(
            u.query
        )

        q = qs.get(
            "q",
            [""]
        )[0]

        sort = qs.get(
            "sort",
            ["views"]
        )[0]

        if sort not in (
            "views",
            "likes",
            "comments",
        ):
            sort = "views"

        c = db()

        count = c.execute(
            "SELECT COUNT(*) FROM videos"
        ).fetchone()[0]

        if q:

            like = "%" + q + "%"

            rows = c.execute(
                f"""
                SELECT *
                FROM videos
                WHERE caption LIKE ?
                   OR hashtags LIKE ?
                   OR username LIKE ?
                ORDER BY {sort} DESC
                LIMIT 300
                """,
                (
                    like,
                    like,
                    like,
                ),
            ).fetchall()

        else:

            rows = c.execute(
                f"""
                SELECT *
                FROM videos
                ORDER BY {sort} DESC
                LIMIT 300
                """
            ).fetchall()

        c.close()

        cards = "".join(

            f"""
            <div class="card">

            <div class="u">
            @{html.escape(r['username'] or 'desconhecido')}
            </div>

            <p class="cap">
            {html.escape(r['caption'] or '')}
            </p>

            <div class="stats">

            <span>
            👁 {r['views'] or 0}
            </span>

            <span>
            ❤️ {r['likes'] or 0}
            </span>

            <span>
            💬 {r['comments'] or 0}
            </span>

            </div>

            <div class="tag">
            {html.escape(r['hashtags'] or '')}
            </div>

            <p>

            <a
            class="open"
            href="{html.escape(r['shopee_url'] or '#')}"
            target="_blank"
            rel="noopener noreferrer"
            >
            Abrir na Shopee
            </a>

            </p>

            </div>
            """

            for r in rows
        )

        if not cards:

            cards = """
            <p class="muted">
            Nenhum vídeo ainda.
            Colete uma hashtag acima.
            </p>
            """

        page = (
            PAGE
            .replace(
                "{{COUNT}}",
                str(count)
            )
            .replace(
                "{{Q}}",
                html.escape(
                    q,
                    quote=True
                )
            )
            .replace(
                "{{CARDS}}",
                cards
            )
        )

        self.send(page)


if __name__ == "__main__":

    db().close()

    port = int(
        os.environ.get(
            "PORT",
            "8787"
        )
    )

    print(
        f"Radar Shopee Vídeo iniciado na porta {port}"
    )

    HTTPServer(
        (
            "0.0.0.0",
            port,
        ),
        H,
    ).serve_forever()
