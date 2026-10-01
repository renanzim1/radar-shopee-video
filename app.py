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
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            product TEXT DEFAULT '',
            video_url TEXT DEFAULT '',
            shopee_url TEXT DEFAULT '',
            source TEXT DEFAULT 'hashtag'
        )
    """)

    existing = {
        row["name"]
        for row in c.execute("PRAGMA table_info(videos)").fetchall()
    }

    extras = {
        "cover": "TEXT DEFAULT ''",
        "product_price": "INTEGER DEFAULT 0",
        "old_price": "INTEGER DEFAULT 0",
        "discount": "INTEGER DEFAULT 0",
        "sold": "INTEGER DEFAULT 0",
        "item_id": "TEXT DEFAULT ''",
        "shop_id": "TEXT DEFAULT ''",
        "product_url": "TEXT DEFAULT ''"
    }

    for name, sql_type in extras.items():
        if name not in existing:
            c.execute(
                f"ALTER TABLE videos ADD COLUMN {name} {sql_type}"
            )

    c.commit()
    return c


# =========================================================
# AUXILIARES
# =========================================================

def number(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except:
        return "0"


def money(value):
    try:
        # Valores da Shopee neste payload usam 100000 unidades
        # para representar R$ 1,00.
        value = int(value or 0) / 100000

        return (
            f"R$ {value:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except:
        return ""


def esc(value):
    return html.escape(str(value or ""))


def esc_attr(value):
    return html.escape(str(value or ""), quote=True)


# =========================================================
# EXTRAÇÃO
# =========================================================

def extract_video(item):

    meta = item.get("meta") or {}
    content = item.get("content") or {}

    pid = meta.get("postId")

    if not pid:
        return None

    user_id = meta.get("userId") or ""
    username = meta.get("userName") or ""

    caption = content.get("caption") or ""

    # ---------------- MÉTRICAS ----------------

    count = meta.get("countInfo") or {}

    views = int(count.get("views") or 0)
    likes = int(count.get("likes") or 0)
    comments = int(count.get("comments") or 0)

    # ---------------- HASHTAGS ----------------

    hashtags = re.findall(
        r"#([\wÀ-ÿ]+)",
        caption
    )

    # ---------------- VÍDEO ----------------

    video = content.get("video") or {}

    cover = (
        video.get("cover")
        or video.get("mmsCover")
        or ""
    )

    video_url = (
        video.get("url")
        or video.get("watermarkVideoUrl")
        or ""
    )

    # ---------------- PRODUTO ----------------

    products = content.get("products") or {}

    product = (
        products.get("anchorProduct")
        or {}
    )

    product_name = product.get("name") or ""

    item_id = product.get("itemId") or ""
    shop_id = product.get("shopId") or ""

    product_price = int(
        product.get("price") or 0
    )

    old_price = int(
        product.get("priceBeforeDiscount") or 0
    )

    discount = 0
    sold = 0

    enhanced = products.get(
        "enhancedItemList"
    ) or []

    if enhanced:

        ep = enhanced[0]

        if not product_name:
            product_name = ep.get("name") or ""

        if not product_price:
            product_price = int(
                ep.get("price") or 0
            )

        if not old_price:
            old_price = int(
                ep.get("priceBeforeDiscount") or 0
            )

        discount = int(
            ep.get("discount") or 0
        )

        sold = int(
            ep.get("historicalSold")
            or ep.get("sold")
            or 0
        )

    # ---------------- LINK DO PRODUTO ----------------
    #
    # itemId e shopId são fornecidos pelo próprio payload.
    # Link padrão da página do produto.

    product_url = ""

    if item_id and shop_id:
        product_url = (
            "https://shopee.com.br/product/"
            f"{shop_id}/{item_id}"
        )

    return {
        "post_id": str(pid),
        "user_id": str(user_id),
        "username": str(username),
        "caption": str(caption),
        "hashtags": ",".join(hashtags),

        "views": views,
        "likes": likes,
        "comments": comments,

        "product": str(product_name),

        "video_url": str(video_url),
        "cover": str(cover),

        # Ainda não fabricamos link do post.
        "shopee_url": "",

        "source": "hashtag",

        "product_price": product_price,
        "old_price": old_price,
        "discount": discount,
        "sold": sold,

        "item_id": str(item_id),
        "shop_id": str(shop_id),
        "product_url": product_url
    }


def find_items(obj, output):

    if isinstance(obj, dict):

        meta = obj.get("meta")
        content = obj.get("content")

        # Esta é a estrutura que confirmamos no diagnóstico.
        if (
            isinstance(meta, dict)
            and isinstance(content, dict)
            and meta.get("postId")
        ):

            record = extract_video(obj)

            if record:
                output[record["post_id"]] = record

            # Não precisamos interpretar novamente o mesmo
            # item por dentro.
            return

        for value in obj.values():
            find_items(value, output)

    elif isinstance(obj, list):

        for value in obj:
            find_items(value, output)


# =========================================================
# COLETA
# =========================================================

def collect(tag):

    tag = tag.strip().lstrip("#")

    if not tag:
        return 0

    url = (
        "https://sv.shopee.com.br/web/hashtag/"
        + urllib.parse.quote(tag)
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0 (Linux; Android 15) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140 Mobile Safari/537.36",

            "Accept":
                "text/html,application/xhtml+xml",

            "Accept-Language":
                "pt-BR,pt;q=0.9,en;q=0.8"
        }
    )

    raw = urllib.request.urlopen(
        req,
        timeout=30
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
        raise Exception(
            "A Shopee não retornou o bloco __NEXT_DATA__."
        )

    raw_json = match.group(1)

    try:
        data = json.loads(raw_json)
    except:
        data = json.loads(
            html.unescape(raw_json)
        )

    videos = {}

    find_items(
        data,
        videos
    )

    if not videos:
        raise Exception(
            "Nenhum vídeo foi encontrado nessa hashtag."
        )

    c = db()

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
                cover,
                product_price,
                old_price,
                discount,
                sold,
                item_id,
                shop_id,
                product_url
            )

            VALUES(
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?
            )

            ON CONFLICT(post_id) DO UPDATE SET

                user_id = excluded.user_id,
                username = excluded.username,
                caption = excluded.caption,
                hashtags = excluded.hashtags,

                views = excluded.views,
                likes = excluded.likes,
                comments = excluded.comments,

                product = excluded.product,

                video_url =
                    CASE
                        WHEN excluded.video_url != ''
                        THEN excluded.video_url
                        ELSE videos.video_url
                    END,

                cover =
                    CASE
                        WHEN excluded.cover != ''
                        THEN excluded.cover
                        ELSE videos.cover
                    END,

                product_price = excluded.product_price,
                old_price = excluded.old_price,
                discount = excluded.discount,
                sold = excluded.sold,

                item_id = excluded.item_id,
                shop_id = excluded.shop_id,

                product_url =
                    CASE
                        WHEN excluded.product_url != ''
                        THEN excluded.product_url
                        ELSE videos.product_url
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
            r["source"],
            r["cover"],

            r["product_price"],
            r["old_price"],
            r["discount"],
            r["sold"],

            r["item_id"],
            r["shop_id"],
            r["product_url"]
        ))

    c.commit()
    c.close()

    return len(videos)


# =========================================================
# HTML
# =========================================================

PAGE = """
<!doctype html>

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
    margin:0;
    background:#f5f6f8;
    color:#171717;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}

.wrap{
    width:min(1100px,100%);
    margin:auto;
    padding:18px;
}

.hero{
    background:#ee4d2d;
    color:#fff;
    padding:26px;
    border-radius:22px;
}

.hero h1{
    margin:0 0 8px;
    font-size:clamp(32px,7vw,54px);
    line-height:1;
}

.hero p{
    margin:0;
    font-size:18px;
}

.collect{
    display:flex;
    gap:9px;
    margin-top:20px;
}

input,
select,
button{
    border:0;
    border-radius:13px;
    font:inherit;
}

input,
select{
    background:#fff;
    padding:14px;
}

input{
    min-width:0;
    flex:1;
}

button{
    padding:14px 20px;
    background:#111;
    color:#fff;
    font-weight:800;
    cursor:pointer;
}

.toolbar{
    margin:20px 0;
}

.total{
    font-size:21px;
    font-weight:900;
    margin-bottom:12px;
}

.search{
    display:grid;
    grid-template-columns:1fr auto auto;
    gap:8px;
}

.grid{
    display:grid;
    grid-template-columns:
        repeat(auto-fill,minmax(270px,1fr));
    gap:16px;
}

.card{
    background:#fff;
    border-radius:20px;
    overflow:hidden;
    box-shadow:
        0 4px 18px rgba(0,0,0,.06);
}

.media{
    position:relative;
    background:#e9e9e9;
    aspect-ratio:9/12;
    overflow:hidden;
}

.cover{
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}

.play{
    position:absolute;
    left:50%;
    top:50%;
    transform:translate(-50%,-50%);

    width:64px;
    height:64px;

    border-radius:50%;

    background:rgba(0,0,0,.72);
    color:#fff;

    display:flex;
    align-items:center;
    justify-content:center;

    font-size:26px;

    cursor:pointer;
    border:0;
}

video{
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
    background:#000;
}

.body{
    padding:16px;
}

.username{
    font-size:18px;
    font-weight:900;
}

.caption{
    color:#444;
    line-height:1.35;

    display:-webkit-box;
    -webkit-line-clamp:3;
    -webkit-box-orient:vertical;

    overflow:hidden;
}

.stats{
    display:flex;
    gap:15px;
    margin:13px 0;
    font-weight:900;
}

.product{
    background:#fff1ed;
    border-radius:12px;
    padding:12px;
    margin-top:10px;
}

.product-name{
    font-weight:700;

    display:-webkit-box;
    -webkit-line-clamp:2;
    -webkit-box-orient:vertical;

    overflow:hidden;
}

.price-line{
    display:flex;
    gap:8px;
    align-items:center;
    flex-wrap:wrap;
    margin-top:8px;
}

.price{
    color:#ee4d2d;
    font-size:19px;
    font-weight:900;
}

.old{
    color:#888;
    text-decoration:line-through;
    font-size:13px;
}

.discount{
    background:#ee4d2d;
    color:#fff;
    font-size:12px;
    padding:3px 7px;
    border-radius:6px;
    font-weight:800;
}

.sold{
    margin-top:6px;
    color:#666;
    font-size:13px;
}

.tags{
    color:#ee4d2d;
    font-size:12px;
    margin-top:11px;
    overflow:hidden;
}

.product-link{
    display:block;
    margin-top:13px;
    padding:12px;

    background:#ee4d2d;
    color:#fff;

    text-align:center;
    text-decoration:none;

    border-radius:11px;
    font-weight:900;
}

.empty{
    background:#fff;
    border-radius:16px;
    padding:25px;
}

@media(max-width:650px){

    .wrap{
        padding:14px;
    }

    .hero{
        padding:22px;
    }

    .collect{
        display:grid;
        grid-template-columns:1fr auto;
    }

    .search{
        grid-template-columns:1fr auto;
    }

    .search input{
        grid-column:1/-1;
    }

}

</style>

</head>

<body>

<div class="wrap">

    <section class="hero">

        <h1>
            Radar Shopee Vídeo 🇧🇷
        </h1>

        <p>
            Descubra e organize vídeos públicos
            por nicho/hashtag.
        </p>

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

    </section>

    <section class="toolbar">

        <div class="total">
            {{COUNT}} vídeos no banco
        </div>

        <form
            class="search"
            method="get"
            action="/"
        >

            <input
                name="q"
                value="{{Q}}"
                placeholder="Buscar criador, produto ou legenda"
            >

            <select name="sort">

                <option value="views" {{SV}}>
                    Mais vistos
                </option>

                <option value="likes" {{SL}}>
                    Mais curtidos
                </option>

                <option value="comments" {{SC}}>
                    Mais comentados
                </option>

            </select>

            <button>
                Buscar
            </button>

        </form>

    </section>

    <section class="grid">
        {{CARDS}}
    </section>

</div>

<script>

function playVideo(button){

    const box = button.parentElement;

    const url = button.dataset.video;

    if(!url){
        return;
    }

    box.innerHTML = "";

    const video =
        document.createElement("video");

    video.src = url;
    video.controls = true;
    video.autoplay = true;
    video.playsInline = true;

    box.appendChild(video);
}

</script>

</body>
</html>
"""


# =========================================================
# CARD
# =========================================================

def make_card(r):

    cover = r["cover"] or ""
    video_url = r["video_url"] or ""

    if cover:

        media = (
            '<div class="media">'
            '<img class="cover" loading="lazy" src="'
            + esc_attr(cover)
            + '">'
        )

        if video_url:

            media += (
                '<button class="play" '
                'data-video="'
                + esc_attr(video_url)
                + '" '
                'onclick="playVideo(this)">'
                '▶'
                '</button>'
            )

        media += "</div>"

    elif video_url:

        media = (
            '<div class="media">'
            '<button class="play" '
            'data-video="'
            + esc_attr(video_url)
            + '" '
            'onclick="playVideo(this)">'
            '▶'
            '</button>'
            '</div>'
        )

    else:

        media = (
            '<div class="media"></div>'
        )

    # ---------------- PRODUTO ----------------

    product_html = ""

    if r["product"]:

        product_html = (
            '<div class="product">'
            '<div class="product-name">🛍️ '
            + esc(r["product"])
            + '</div>'
        )

        if r["product_price"]:

            product_html += (
                '<div class="price-line">'
                '<span class="price">'
                + money(r["product_price"])
                + '</span>'
            )

            if (
                r["old_price"]
                and r["old_price"] > r["product_price"]
            ):

                product_html += (
                    '<span class="old">'
                    + money(r["old_price"])
                    + '</span>'
                )

            if r["discount"]:

                product_html += (
                    '<span class="discount">'
                    + str(r["discount"])
                    + '% OFF</span>'
                )

            product_html += "</div>"

        if r["sold"]:

            product_html += (
                '<div class="sold">'
                + number(r["sold"])
                + ' vendidos'
                '</div>'
            )

        product_html += "</div>"

    # ---------------- BOTÃO ----------------

    product_button = ""

    if r["product_url"]:

        product_button = (
            '<a class="product-link" '
            'target="_blank" '
            'rel="noopener" '
            'href="'
            + esc_attr(r["product_url"])
            + '">'
            '🛒 Abrir produto na Shopee'
            '</a>'
        )

    hashtags = r["hashtags"] or ""

    if hashtags:

        hashtags = " #" + hashtags.replace(
            ",",
            " #"
        )

    return (
        '<article class="card">'
        + media
        + '<div class="body">'

        + '<div class="username">@'
        + esc(r["username"] or "desconhecido")
        + '</div>'

        + '<p class="caption">'
        + esc(r["caption"])
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
        + esc(hashtags)
        + '</div>'

        + product_button

        + '</div>'
        + '</article>'
    )


# =========================================================
# SERVIDOR
# =========================================================

class H(BaseHTTPRequestHandler):

    def send_html(self, text, code=200):

        self.send_response(code)

        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            text.encode("utf-8")
        )

    def do_POST(self):

        parsed = urllib.parse.urlparse(
            self.path
        )

        if parsed.path != "/collect":

            self.send_html(
                "Não encontrado",
                404
            )

            return

        length = int(
            self.headers.get(
                "Content-Length",
                "0"
            )
        )

        body = self.rfile.read(
            length
        ).decode(
            "utf-8",
            "ignore"
        )

        form = urllib.parse.parse_qs(
            body
        )

        tag = form.get(
            "tag",
            [""]
        )[0]

        try:

            collect(tag)

            self.send_response(303)

            self.send_header(
                "Location",
                "/"
            )

            self.end_headers()

        except Exception as e:

            self.send_html(
                "<meta name='viewport' "
                "content='width=device-width,initial-scale=1'>"
                "<div style='font-family:system-ui;padding:20px'>"
                "<h2>Erro na coleta</h2>"
                "<pre style='white-space:pre-wrap'>"
                + esc(str(e))
                + "</pre>"
                "<p><a href='/'>Voltar</a></p>"
                "</div>",
                500
            )

    def do_GET(self):

        parsed = urllib.parse.urlparse(
            self.path
        )

        if parsed.path != "/":

            self.send_html(
                "Não encontrado",
                404
            )

            return

        qs = urllib.parse.parse_qs(
            parsed.query
        )

        q = qs.get(
            "q",
            [""]
        )[0].strip()

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

        count = c.execute(
            "SELECT COUNT(*) FROM videos"
        ).fetchone()[0]

        if q:

            like = "%" + q + "%"

            rows = c.execute(
                f"""
                SELECT *
                FROM videos

                WHERE
                    username LIKE ?
                    OR caption LIKE ?
                    OR hashtags LIKE ?
                    OR product LIKE ?

                ORDER BY {sort} DESC

                LIMIT 500
                """,
                (
                    like,
                    like,
                    like,
                    like
                )
            ).fetchall()

        else:

            rows = c.execute(
                f"""
                SELECT *
                FROM videos
                ORDER BY {sort} DESC
                LIMIT 500
                """
            ).fetchall()

        cards = "".join(
            make_card(r)
            for r in rows
        )

        c.close()

        if not cards:

            cards = (
                '<div class="empty">'
                'Nenhum vídeo encontrado.'
                '</div>'
            )

        page = PAGE

        page = page.replace(
            "{{COUNT}}",
            str(count)
        )

        page = page.replace(
            "{{Q}}",
            esc_attr(q)
        )

        page = page.replace(
            "{{CARDS}}",
            cards
        )

        page = page.replace(
            "{{SV}}",
            "selected"
            if sort == "views"
            else ""
        )

        page = page.replace(
            "{{SL}}",
            "selected"
            if sort == "likes"
            else ""
        )

        page = page.replace(
            "{{SC}}",
            "selected"
            if sort == "comments"
            else ""
        )

        self.send_html(page)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    db().close()

    print(
        f"Radar Shopee rodando na porta {PORT}"
    )

    HTTPServer(
        ("0.0.0.0", PORT),
        H
    ).serve_forever()
