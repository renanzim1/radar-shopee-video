import os
import json
import sqlite3
import urllib.parse
import urllib.request
import html
import re

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


DB = Path(__file__).with_name("radar.db")
PORT = int(os.environ.get("PORT", "8787"))

# Guarda temporariamente o último diagnóstico em memória.
LAST_DEBUG = {
    "tag": "",
    "url": "",
    "video": None,
    "error": ""
}


# =========================================================
# BANCO
# =========================================================

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    c.execute("""
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
    """)

    return c


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def to_int(value):
    try:
        if value is None:
            return 0

        if isinstance(value, bool):
            return int(value)

        if isinstance(value, (int, float)):
            return int(value)

        value = str(value).strip()

        if not value:
            return 0

        return int(float(value))

    except:
        return 0


def first_value(obj, names):
    """
    Procura recursivamente um campo dentro de dict/list.
    """

    if isinstance(obj, dict):

        for name in names:
            if name in obj:
                value = obj.get(name)

                if value not in (None, "", [], {}):
                    return value

        for value in obj.values():
            result = first_value(value, names)

            if result not in (None, "", [], {}):
                return result

    elif isinstance(obj, list):

        for value in obj:
            result = first_value(value, names)

            if result not in (None, "", [], {}):
                return result

    return None


def max_metric(obj, names):
    """
    Procura todas as ocorrências possíveis de uma métrica
    dentro do bloco do vídeo e fica com o maior valor.
    """

    values = []

    normalized_names = {
        re.sub(r"[^a-z0-9]", "", x.lower())
        for x in names
    }

    def scan(x):

        if isinstance(x, dict):

            for key, value in x.items():

                normalized_key = re.sub(
                    r"[^a-z0-9]",
                    "",
                    str(key).lower()
                )

                if normalized_key in normalized_names:
                    values.append(to_int(value))

                scan(value)

        elif isinstance(x, list):

            for value in x:
                scan(value)

    scan(obj)

    return max(values) if values else 0


def find_video_container(obj):
    """
    Procura um bloco que aparentemente representa
    um vídeo/post completo.
    """

    if isinstance(obj, dict):

        meta = obj.get("meta")

        if isinstance(meta, dict):

            pid = (
                meta.get("postId")
                or meta.get("post_id")
            )

            if pid:
                return obj

        # Alguns formatos podem não possuir "meta"
        pid = (
            obj.get("postId")
            or obj.get("post_id")
        )

        if pid and len(obj) > 2:
            return obj

        for value in obj.values():

            result = find_video_container(value)

            if result:
                return result

    elif isinstance(obj, list):

        for value in obj:

            result = find_video_container(value)

            if result:
                return result

    return None


def extract_video(container):

    meta = container.get("meta")

    if not isinstance(meta, dict):
        meta = container

    content = container.get("content")

    if not isinstance(content, dict):
        content = {}

    pid = (
        meta.get("postId")
        or meta.get("post_id")
        or first_value(container, ["postId", "post_id"])
    )

    uid = (
        meta.get("userId")
        or meta.get("user_id")
        or first_value(container, ["userId", "user_id"])
    )

    username = (
        meta.get("userName")
        or meta.get("username")
        or first_value(
            container,
            ["userName", "username"]
        )
        or ""
    )

    caption = (
        content.get("caption")
        or meta.get("caption")
        or first_value(container, ["caption"])
        or ""
    )

    views = max_metric(
        container,
        [
            "views",
            "viewCount",
            "view_count",
            "viewCnt",
            "view_cnt",
            "playCount",
            "play_count"
        ]
    )

    likes = max_metric(
        container,
        [
            "likes",
            "likeCount",
            "like_count",
            "likeCnt",
            "like_cnt"
        ]
    )

    comments = max_metric(
        container,
        [
            "comments",
            "commentCount",
            "comment_count",
            "commentCnt",
            "comment_cnt"
        ]
    )

    hashtags = re.findall(
        r"#([\wÀ-ÿ]+)",
        str(caption)
    )

    # -----------------------------------------------------
    # CAPA
    # -----------------------------------------------------

    cover = first_value(
        content,
        [
            "coverUrl",
            "cover_url",
            "cover",
            "thumbnailUrl",
            "thumbnail_url",
            "thumbnail",
            "imageUrl",
            "image_url"
        ]
    )

    if isinstance(cover, dict):
        cover = first_value(
            cover,
            ["url", "imageUrl", "image_url"]
        )

    if isinstance(cover, list):
        cover = cover[0] if cover else ""

    # -----------------------------------------------------
    # URL DO VÍDEO
    # -----------------------------------------------------

    video_url = ""

    video = content.get("video")

    if isinstance(video, dict):

        video_url = (
            video.get("url")
            or video.get("videoUrl")
            or video.get("video_url")
            or first_value(
                video,
                ["videoUrl", "video_url", "url"]
            )
            or ""
        )

    # -----------------------------------------------------
    # LINK SHOPEE
    #
    # Não fabricamos mais o share-video.
    # Procuramos URL fornecida pelo próprio payload.
    # -----------------------------------------------------

    shopee_url = first_value(
        container,
        [
            "shareUrl",
            "share_url",
            "deeplink",
            "deepLink",
            "webUrl",
            "web_url",
            "postUrl",
            "post_url"
        ]
    )

    if not isinstance(shopee_url, str):
        shopee_url = ""

    # -----------------------------------------------------
    # PRODUTO
    # -----------------------------------------------------

    product = ""

    product_block = first_value(
        container,
        [
            "anchorProduct",
            "anchor_product",
            "product"
        ]
    )

    if isinstance(product_block, dict):

        product = (
            product_block.get("itemName")
            or product_block.get("item_name")
            or product_block.get("name")
            or product_block.get("title")
            or ""
        )

    return {
        "post_id": str(pid or ""),
        "user_id": str(uid or ""),
        "username": str(username or ""),
        "caption": str(caption or ""),
        "hashtags": ",".join(hashtags),
        "views": views,
        "likes": likes,
        "comments": comments,
        "product": str(product or ""),
        "video_url": str(video_url or ""),
        "shopee_url": str(shopee_url or ""),
        "cover": str(cover or "")
    }


# =========================================================
# COLETA
# =========================================================

def collect(tag):

    global LAST_DEBUG

    tag = tag.strip().lstrip("#")

    url = (
        "https://sv.shopee.com.br/web/hashtag/"
        + urllib.parse.quote(tag)
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0 (Linux; Android 15) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36",

            "Accept":
                "text/html,application/xhtml+xml",

            "Accept-Language":
                "pt-BR,pt;q=0.9,en;q=0.8"
        }
    )

    raw = urllib.request.urlopen(
        req,
        timeout=25
    ).read().decode(
        "utf-8",
        "ignore"
    )

    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>'
        r'(.*?)'
        r'</script>',
        raw,
        re.S
    )

    if not match:

        LAST_DEBUG = {
            "tag": tag,
            "url": url,
            "video": None,
            "error": "__NEXT_DATA__ não encontrado"
        }

        return 0, "__NEXT_DATA__ não encontrado"

    raw_json = match.group(1)

    try:
        data = json.loads(raw_json)

    except:
        data = json.loads(
            html.unescape(raw_json)
        )

    # -----------------------------------------------------
    # ENCONTRA PRIMEIRO VÍDEO COMPLETO
    # -----------------------------------------------------

    container = find_video_container(data)

    if not container:

        LAST_DEBUG = {
            "tag": tag,
            "url": url,
            "video": None,
            "error": "Nenhum bloco de vídeo encontrado"
        }

        return 0, "Nenhum vídeo encontrado"

    # Guarda o bloco bruto para diagnóstico.
    LAST_DEBUG = {
        "tag": tag,
        "url": url,
        "video": container,
        "error": ""
    }

    # -----------------------------------------------------
    # PROCURA TODOS OS CONTAINERS
    # -----------------------------------------------------

    containers = []

    def scan(x):

        if isinstance(x, dict):

            meta = x.get("meta")

            if isinstance(meta, dict):

                pid = (
                    meta.get("postId")
                    or meta.get("post_id")
                )

                if pid:
                    containers.append(x)

            for value in x.values():
                scan(value)

        elif isinstance(x, list):

            for value in x:
                scan(value)

    scan(data)

    # -----------------------------------------------------
    # REMOVE DUPLICADOS
    # -----------------------------------------------------

    videos = {}

    for container in containers:

        record = extract_video(container)

        pid = record["post_id"]

        if not pid:
            continue

        if pid not in videos:
            videos[pid] = record

        else:
            old = videos[pid]

            old["views"] = max(
                old["views"],
                record["views"]
            )

            old["likes"] = max(
                old["likes"],
                record["likes"]
            )

            old["comments"] = max(
                old["comments"],
                record["comments"]
            )

            for field in [
                "username",
                "caption",
                "hashtags",
                "product",
                "video_url",
                "shopee_url",
                "cover"
            ]:

                if not old.get(field) and record.get(field):
                    old[field] = record[field]

    # -----------------------------------------------------
    # BANCO
    #
    # O banco atual não possui coluna cover.
    # Criamos se necessário.
    # -----------------------------------------------------

    c = db()

    columns = [
        x["name"]
        for x in c.execute(
            "PRAGMA table_info(videos)"
        ).fetchall()
    ]

    if "cover" not in columns:
        c.execute(
            "ALTER TABLE videos ADD COLUMN cover TEXT DEFAULT ''"
        )

    for r in videos.values():

        c.execute("""
            INSERT INTO videos(
                post_id,
                user_id,
                username,
                caption,
                hashtags,
                views,
                likes,
                comments,
                product,
                video_url,
                shopee_url,
                source,
                cover
            )

            VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?
            )

            ON CONFLICT(post_id) DO UPDATE SET

                user_id =
                    CASE
                        WHEN excluded.user_id != ''
                        THEN excluded.user_id
                        ELSE videos.user_id
                    END,

                username =
                    CASE
                        WHEN excluded.username != ''
                        THEN excluded.username
                        ELSE videos.username
                    END,

                caption =
                    CASE
                        WHEN excluded.caption != ''
                        THEN excluded.caption
                        ELSE videos.caption
                    END,

                hashtags =
                    CASE
                        WHEN excluded.hashtags != ''
                        THEN excluded.hashtags
                        ELSE videos.hashtags
                    END,

                views =
                    MAX(videos.views, excluded.views),

                likes =
                    MAX(videos.likes, excluded.likes),

                comments =
                    MAX(videos.comments, excluded.comments),

                product =
                    CASE
                        WHEN excluded.product != ''
                        THEN excluded.product
                        ELSE videos.product
                    END,

                video_url =
                    CASE
                        WHEN excluded.video_url != ''
                        THEN excluded.video_url
                        ELSE videos.video_url
                    END,

                shopee_url =
                    CASE
                        WHEN excluded.shopee_url != ''
                        THEN excluded.shopee_url
                        ELSE videos.shopee_url
                    END,

                cover =
                    CASE
                        WHEN excluded.cover != ''
                        THEN excluded.cover
                        ELSE videos.cover
                    END

        """, (
            r["post_id"],
            r["user_id"],
            r["username"],
            r["caption"],
            r["hashtags"],
            r["views"],
            r["likes"],
            r["comments"],
            r["product"],
            r["video_url"],
            r["shopee_url"],
            "hashtag",
            r["cover"]
        ))

    c.commit()
    c.close()

    return len(videos), url


# =========================================================
# INTERFACE
# =========================================================

PAGE = """
<!doctype html>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>Radar Shopee Vídeo</title>

<style>

*{
    box-sizing:border-box
}

body{
    font-family:system-ui,-apple-system,sans-serif;
    background:#f5f6f8;
    margin:0;
    color:#171717
}

.wrap{
    max-width:1100px;
    margin:auto;
    padding:18px
}

.hero{
    background:#ee4d2d;
    color:white;
    padding:24px;
    border-radius:20px
}

.hero h1{
    margin:0 0 8px
}

.collect{
    display:flex;
    gap:8px;
    margin-top:16px
}

input,
select,
button{
    padding:13px;
    border:0;
    border-radius:12px;
    font-size:16px
}

input{
    flex:1;
    min-width:0
}

button{
    background:#111;
    color:white;
    font-weight:700
}

.bar{
    display:flex;
    gap:10px;
    margin:18px 0;
    align-items:center;
    flex-wrap:wrap
}

.search{
    display:flex;
    gap:8px;
    flex:1;
    min-width:280px
}

.grid{
    display:grid;
    grid-template-columns:
        repeat(auto-fill,minmax(250px,1fr));
    gap:14px
}

.card{
    background:white;
    border-radius:17px;
    overflow:hidden;
    box-shadow:0 2px 12px #00000010
}

.cover{
    width:100%;
    aspect-ratio:9/12;
    object-fit:cover;
    background:#e9e9e9;
    display:block
}

.no-cover{
    width:100%;
    aspect-ratio:9/12;
    background:#e9e9e9;
    display:flex;
    align-items:center;
    justify-content:center;
    color:#888;
    font-weight:700
}

.body{
    padding:15px
}

.user{
    font-weight:800
}

.caption{
    min-height:42px;
    max-height:65px;
    overflow:hidden;
    color:#444
}

.stats{
    display:flex;
    gap:13px;
    font-weight:800;
    margin:12px 0;
    flex-wrap:wrap
}

.product{
    font-size:13px;
    background:#fff2ef;
    padding:9px;
    border-radius:9px;
    margin-bottom:10px
}

.tags{
    font-size:12px;
    color:#ee4d2d
}

.open{
    display:block;
    text-align:center;
    text-decoration:none;
    background:#ee4d2d;
    color:white;
    padding:11px;
    border-radius:10px;
    margin-top:13px;
    font-weight:800
}

.disabled{
    display:block;
    text-align:center;
    background:#ddd;
    color:#777;
    padding:11px;
    border-radius:10px;
    margin-top:13px;
    font-weight:700
}

.debug{
    display:inline-block;
    margin-top:13px;
    color:white;
    font-weight:700
}

pre{
    white-space:pre-wrap;
    word-break:break-word;
    background:#111;
    color:#eaeaea;
    padding:16px;
    border-radius:15px
}

</style>

<div class="wrap">

    <div class="hero">

        <h1>Radar Shopee Vídeo 🇧🇷</h1>

        <div>
            Descubra vídeos públicos por nicho/hashtag.
        </div>

        <form
            class="collect"
            method="post"
            action="/collect"
        >

            <input
                name="tag"
                placeholder="Ex.: cozinha, maquiagem, gokoco"
                required
            >

            <button>
                Coletar
            </button>

        </form>

        <a
            class="debug"
            href="/debug"
        >
            🔧 Ver diagnóstico
        </a>

    </div>

    <div class="bar">

        <b>
            {{COUNT}} vídeos no banco
        </b>

        <form
            class="search"
            method="get"
            action="/"
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

            <button>
                Buscar
            </button>

        </form>

    </div>

    <div class="grid">
        {{CARDS}}
    </div>

</div>
"""


def number(n):
    try:
        return f"{int(n):,}".replace(",", ".")
    except:
        return "0"


# =========================================================
# SERVIDOR
# =========================================================

class H(BaseHTTPRequestHandler):

    def send_html(self, content, code=200):

        self.send_response(code)

        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            content.encode("utf-8")
        )

    def do_POST(self):

        if self.path != "/collect":
            self.send_html("Não encontrado", 404)
            return

        size = int(
            self.headers.get(
                "Content-Length",
                "0"
            )
        )

        body = self.rfile.read(size).decode()

        form = urllib.parse.parse_qs(body)

        tag = form.get(
            "tag",
            [""]
        )[0]

        try:

            collect(tag)

            # Não filtra automaticamente pela hashtag.
            # Assim os cards aparecem imediatamente.
            self.send_response(303)

            self.send_header(
                "Location",
                "/"
            )

            self.end_headers()

        except Exception as e:

            global LAST_DEBUG

            LAST_DEBUG["error"] = str(e)

            self.send_html(
                "<h2>Erro na coleta</h2>"
                "<pre>"
                + html.escape(str(e))
                + "</pre>",
                500
            )

    def do_GET(self):

        parsed = urllib.parse.urlparse(
            self.path
        )

        # -------------------------------------------------
        # DIAGNÓSTICO
        # -------------------------------------------------

        if parsed.path == "/debug":

            if LAST_DEBUG["video"] is None:

                self.send_html("""
                    <meta name="viewport"
                    content="width=device-width,initial-scale=1">

                    <h2>Diagnóstico</h2>

                    <p>
                    Primeiro volte ao Radar e faça uma coleta.
                    Depois abra esta página novamente.
                    </p>
                """)

                return

            raw = json.dumps(
                LAST_DEBUG["video"],
                ensure_ascii=False,
                indent=2
            )

            self.send_html(
                """
                <meta
                    name="viewport"
                    content="width=device-width,initial-scale=1"
                >

                <style>
                    body{
                        font-family:system-ui;
                        padding:15px
                    }

                    pre{
                        white-space:pre-wrap;
                        word-break:break-word;
                        background:#111;
                        color:#eee;
                        padding:15px;
                        border-radius:12px
                    }
                </style>

                <h2>🔧 Diagnóstico Shopee</h2>

                <p>
                    Hashtag:
                    <b>"""
                + html.escape(LAST_DEBUG["tag"])
                + """</b>
                </p>

                <p>
                    Tire prints desta página e me envie.
                </p>

                <pre>"""
                + html.escape(raw)
                + """</pre>
                """
            )

            return

        # -------------------------------------------------
        # HOME
        # -------------------------------------------------

        qs = urllib.parse.parse_qs(
            parsed.query
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
            "comments"
        ):
            sort = "views"

        c = db()

        columns = [
            x["name"]
            for x in c.execute(
                "PRAGMA table_info(videos)"
            ).fetchall()
        ]

        if "cover" not in columns:

            c.execute(
                "ALTER TABLE videos "
                "ADD COLUMN cover TEXT DEFAULT ''"
            )

            c.commit()

        count = c.execute(
            "SELECT COUNT(*) FROM videos"
        ).fetchone()[0]

        if q:

            rows = c.execute(
                f"""
                SELECT *
                FROM videos
                WHERE
                    caption LIKE ?
                    OR hashtags LIKE ?
                    OR username LIKE ?
                    OR product LIKE ?
                ORDER BY {sort} DESC
                LIMIT 300
                """,
                (
                    "%" + q + "%",
                    "%" + q + "%",
                    "%" + q + "%",
                    "%" + q + "%"
                )
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

        cards = []

        for r in rows:

            cover = r["cover"] or ""

            if cover.startswith("http"):

                image_html = (
                    '<img class="cover" '
                    'loading="lazy" '
                    'src="'
                    + html.escape(
                        cover,
                        quote=True
                    )
                    + '">'
                )

            else:

                image_html = (
                    '<div class="no-cover">'
                    'Sem capa'
                    '</div>'
                )

            product_html = ""

            if r["product"]:

                product_html = (
                    '<div class="product">🛍️ '
                    + html.escape(r["product"])
                    + '</div>'
                )

            if (
                r["shopee_url"]
                and str(
                    r["shopee_url"]
                ).startswith("http")
            ):

                button = (
                    '<a class="open" '
                    'target="_blank" '
                    'rel="noopener" '
                    'href="'
                    + html.escape(
                        r["shopee_url"],
                        quote=True
                    )
                    + '">'
                    'Abrir na Shopee'
                    '</a>'
                )

            else:

                button = (
                    '<span class="disabled">'
                    'Link sendo identificado'
                    '</span>'
                )

            card = (
                '<div class="card">'
                + image_html
                + '<div class="body">'
                + '<div class="user">@'
                + html.escape(
                    r["username"]
                    or "desconhecido"
                )
                + '</div>'
                + '<p class="caption">'
                + html.escape(
                    r["caption"]
                    or ""
                )
                + '</p>'
                + '<div class="stats">'
                + '<span>👁 '
                + number(r["views"])
                + '</span>'
                + '<span>❤️ '
                + number(r["likes"])
                + '</span>'
                + '<span>💬 '
                + number(r["comments"])
                + '</span>'
                + '</div>'
                + product_html
                + '<div class="tags">'
                + html.escape(
                    r["hashtags"]
                    or ""
                )
                + '</div>'
                + button
                + '</div>'
                + '</div>'
            )

            cards.append(card)

        c.close()

        cards_html = "".join(cards)

        if not cards_html:

            cards_html = (
                '<p>Nenhum vídeo encontrado.</p>'
            )

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
                cards_html
            )
        )

        self.send_html(page)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    db().close()

    print(
        f"Radar rodando na porta {PORT}"
    )

    HTTPServer(
        ("0.0.0.0", PORT),
        H
    ).serve_forever()
