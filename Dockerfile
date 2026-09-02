# DeerX'i konteynerde calistirmak.
#
# SECURITY.md ve docs/security.md'nin ana tavsiyesi buydu ve depoda
# karsiligi yoktu: "gercek yalitim istiyorsan DeerX'i bir konteyner icinde
# calistir" deniyor, ama calistirilacak bir imaj tarif edilmiyordu.
#
# BU IMAJ DEERX'IN KENDISINI kosturur. `execution = "docker"` ayari BASKA
# bir sey: o, AJANIN komutlarini ayri bir konteynerde kosturur ve bu imajin
# icinden kullanilamaz (icinde Docker istemcisi yok). Ikisi ayni sorunu iki
# ayri katmanda cozer -- birini secin:
#
#   bu imaj              -> DeerX'in tamami konaktan yalitilir
#   execution = "docker" -> DeerX konakta, ajanin komutlari yalitilir
#
# Kullanim:
#
#   docker build -t deerx .
#
#   # Once hesap. Kimliksiz bir sunucu 0.0.0.0'a BAGLANMAZ (bilerek):
#   # dosya yazip kabuk komutu calistiran bir ucu herkese acmak olurdu.
#   docker run --rm -it -v "$PWD/workspace:/workspace" deerx \
#       deerx user add admin --admin
#
#   # Sonra sunucu:
#   docker run --rm -p 8791:8791 -v "$PWD/workspace:/workspace" deerx
#
# WINDOWS / GIT BASH: yukaridaki `$PWD` KULLANILMAZ. MSYS yolu donusturur,
# Docker onu tanimaz ve sessizce ANONIM BIR BIRIM yaratir -- hata vermez.
# Olculdu: iki ayri `docker run` iki ayri birim aldi, hesap birinde
# olusturuldu, sunucu otekine bakti ve "kullanicisiz sunucu baslatilamaz"
# dedi. Sebep gorunmuyordu. Windows yolunu acikca verin:
#
#   set MSYS_NO_PATHCONV=1
#   docker run --rm -p 8791:8791 -v "C:/yol/workspace:/workspace" deerx
#
# Konteyner icinde root kosuyor. Ajan zaten keyfi kabuk komutu
# calistirabiliyor; sinir konteynerin kendisidir, icindeki kullanici degil.
# Konaga baglanan tek sey /workspace'tir ve orasi korunmaz.

# `slim` DEGIL. Olculdu (ayni gerekce `sandbox_image` icin de yazili):
# slim icinde git, curl, gcc ve make YOK -- ajan ilk `pip install`
# derlemesinde ya da `git init`te duvara carpar.
FROM python:3.13

# uv resmi imajdan kopyalanir. Proje `uv.lock` ile kilitli; pip ile kurmak
# kilidi yok saymak olurdu.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Gomme vektorleri yerel ONNX ile uretilir (`embedding_provider =
# "fastembed"`, varsayilan). Ek kurulmazsa bilgi tabani sessizce `hash`
# yedegine duser ve o yedek yalnizca cevrimdisi duman testi icindir --
# yani anlamsal arama calisiyor gorunur, calismaz.
#
# Tarayici araclari bu imajda YOK: `browser` eki Playwright'in sistem
# kutuphanelerini ve bir Chrome indirmesini gerektirir, imaji gigabaytlarca
# buyutur. Ihtiyaciniz varsa: --build-arg EXTRAS=embed,browser ve ardindan
# `playwright install --with-deps chromium`.
ARG EXTRAS=embed

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Once yalnizca manifest ve kilit: kaynak degistiginde bagimlilik katmani
# yeniden kurulmaz. README de gerekli -- pyproject onu `readme` olarak
# gosteriyor ve olmadan derleme duser.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --extra "$EXTRAS"

COPY . .
RUN uv sync --frozen --extra "$EXTRAS"

ENV PATH="/app/.venv/bin:$PATH" \
    DEERX_WORKSPACE=/workspace

# Calisma alani DISARIDAN baglanir: proje dosyalari, bilgi tabani, kullanici
# hesaplari (.deerx/deerx.db) ve ciktilar konteyner silinince kaybolmasin.
VOLUME ["/workspace"]
WORKDIR /workspace

EXPOSE 8791

# `--host 0.0.0.0`: yayinlanan bir portun ise yaramasi icin sart. Guvenligi
# saglayan sey adres degil, kimlik kapisi -- hic kullanici yoksa sunucu
# baslamayi reddeder.
# `--no-open`: konteynerde acilacak bir tarayici yok.
CMD ["deerx", "serve", "--host", "0.0.0.0", "--no-open"]
