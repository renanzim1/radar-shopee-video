RADAR SHOPEE VIDEO - MVP

1) Instale Python 3.
2) Abra um terminal nesta pasta.
3) Rode: python app.py
4) Abra: http://127.0.0.1:8787
5) Digite uma hashtag, por exemplo: gokoco

O MVP usa apenas bibliotecas padrão do Python. Ele tenta ler o __NEXT_DATA__ público da página de hashtag da Shopee, salva os posts em SQLite e mostra um painel pesquisável.

Observação: a Shopee pode mudar ou limitar o endpoint/página pública. O coletor não contorna login, CAPTCHA ou proteções anti-bot.
