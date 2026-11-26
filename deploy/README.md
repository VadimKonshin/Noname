# Переезд на удалённый сервер

На сервере поднимаются пять частей: сайт на gunicorn, один процесс Telegram-бота, PostgreSQL 16, Redis 7 и Caddy. Caddy получает сертификат Let's Encrypt и отдаёт страницы, `/static/` и `/media/`. Порты базы и Redis наружу не открываются.

Команды Docker на сервере выполняются из каталога `deploy/`. Файл `docker-compose.yml` в корне проекта остаётся для этого компьютера.

## Что нужно заранее

- Домен, у которого A-запись указывает на сервер. Пока своего домена нет, `SITE_ADDRESS` — публичный IP сервера, `ACME_PROFILE=shortlived`, а `DJANGO_SITE_URL` и `DJANGO_CSRF_TRUSTED_ORIGINS` начинаются с `https://`. Caddy берёт для IP короткий сертификат Let's Encrypt, и вход перестаёт идти по голому HTTP. Имя хостера вроде `имя.fvds.ru` сертификат не получает: Let's Encrypt ограничивает всю зону `fvds.ru`.
- Ubuntu с Docker Engine и плагином Compose. Проверка: `docker compose version`.
- Открытые порты 22, 80 и 443. На Ubuntu с ufw:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Пользователь, который запускает Docker, входит в группу `docker`.

## 1. Снять данные с этого компьютера

Локальные PostgreSQL должны быть запущены (`docker compose up -d` в корне проекта).

```bash
python3 deploy/export_local.py
```

Появятся `deploy/bundle/noname.sql`, `deploy/bundle/media.tar.gz` и `deploy/bundle/SHA256SUMS`. Токен бота, пароль базы и почта в архив не попадают. Скрипт напечатает имена переменных, которые нужно переписать в серверный `deploy/.env` вручную.

## 2. Положить проект на сервер

Берите эту папку, а не старый `main`: рабочая копия может быть новее. Каталог на сервере в примерах — `/opt/noname`.

```bash
rsync -a \
  --exclude .venv --exclude .git --exclude media --exclude staticfiles \
  --exclude db.sqlite3 --exclude .env --exclude deploy/.env --exclude deploy/backups \
  ./ user@SERVER:/opt/noname/
```

`.env` этим копированием не передаётся. На сервере его создают отдельно, чтобы пароль локальной базы не затёр серверный при следующем обновлении.

## 3. Заполнить deploy/.env

На сервере:

```bash
cd /opt/noname/deploy
cp .env.example .env
```

Замените каждый `shop.example.com` на свой домен. Одно и то же имя стоит в `SITE_ADDRESS`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` и `DJANGO_SITE_URL`.

Секрет и пароли:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
python3 -c "import secrets,string; a=string.ascii_letters+string.digits; print(''.join(secrets.choice(a) for _ in range(32)))"
```

Первая строка — `DJANGO_SECRET_KEY`. Вторую запустите дважды: для `DJANGO_DB_PASSWORD` и для `REDIS_PASSWORD`. В паролях только буквы и цифры. Знак `$` Compose съедает.

Туда же перенесите токен и имя бота, ссылку группы, SMTP и ключи СДЭК, если они уже есть. `DJANGO_EMAIL_HOST` нужен, чтобы письма уходили на ящики, а не в лог контейнера. `DJANGO_ORDER_EMAIL` — живой адрес администратора. `DJANGO_DEFAULT_FROM_EMAIL` — отправитель на вашем домене.

Проверка файла:

```bash
python3 check_env.py .env
```

Пока в файле остаётся `shop.example.com` или пустой пароль, команда завершится с ошибкой и контейнеры не стартуют.

## 4. Загрузить базу и фотографии

DNS уже должен указывать на сервер, иначе Caddy не получит сертификат.

```bash
cd /opt/noname/deploy
python3 import_remote.py --replace
```

Команда собирает образ, заменяет базу содержимым дампа, поднимает сайт и раскладывает фотографии. Аккаунты из дампа сохраняются, отдельный `createsuperuser` не нужен.

Перед этим остановите локальный `runserver`. Иначе бот на ноутбуке и бот на сервере делят одни и те же обновления Telegram.

Сайт открывается по `https://ваш-домен/`. В журнале Caddy может быть предупреждение, что заголовки `X-Forwarded-For` и `X-Forwarded-Proto` лишние. Эти строки в `Caddyfile` оставлены специально: они подменяют адрес, который прислал посетитель.

Журналы:

```bash
docker compose logs -f caddy web bot
```

## Пустая база

Если переносить текущие заказы и фотографии не нужно:

```bash
cd /opt/noname/deploy
docker compose up -d --build
docker compose exec -u app web python manage.py createsuperuser
```

## Проверка

- Главная, каталог и вход в `/admin/` открываются по HTTPS.
- Загрузка фотографии в товар сохраняется и видна на витрине.
- `docker compose exec -u app web python manage.py ssl_status` показывает состояние HTTPS.
- Письмо подтверждения приходит на ящик, если заполнен SMTP. Ссылка в нём начинается с `DJANGO_SITE_URL`.

HSTS на поддоменах и preload остаются выключенными. Их включают в `.env` только когда все поддомены точно открываются по HTTPS.

## Копии каждый день

На сервере, от пользователя, у которого есть доступ к Docker:

```bash
0 4 * * * cd /opt/noname/deploy && /usr/bin/python3 backup.py >> /var/log/noname-backup.log 2>&1
```

Скрипт пишет `deploy/backups/db-ГГГГММДД-ЧЧММ.sql.gz` и такой же архив фотографий, и удаляет файлы старше 14 дней. `docker compose down` тома не удаляет. `docker compose down -v` удаляет базу, фотографии и сертификат.

## Обновление кода

На этом компьютере остановите `runserver`, если бот уже работает на сервере. Затем:

```bash
rsync -a \
  --exclude .venv --exclude .git --exclude media --exclude staticfiles \
  --exclude db.sqlite3 --exclude .env --exclude deploy/.env \
  --exclude deploy/backups --exclude deploy/bundle \
  ./ user@SERVER:/opt/noname/
```

На сервере:

```bash
cd /opt/noname/deploy
docker compose up -d --build
```

Миграции применяются при старте `web`. Слушатель бота по-прежнему один: контейнер `bot`.

## GitHub Actions

Файл `.github/workflows/deploy.yml` на каждый push в `main` прогоняет тесты и, если они прошли, копирует код на `92.118.88.34` и пересобирает контейнеры. Ручной запуск — кнопка Run workflow в GitHub. База, фотографии и `deploy/.env` при этом не затираются.

Секрет `SSH_PRIVATE_KEY` — содержимое файла `~/.ssh/noname_deploy` на этом компьютере, вместе со строками `BEGIN` и `END`. Его место: GitHub → репозиторий Noname → Settings → Secrets and variables → Actions → New repository secret. Публичная половина этого ключа уже стоит на сервере.

Пока текущий код не влит в `main`, автоматический деплой запускать не стоит: он выложит на сервер ту версию, которая есть в GitHub, и заменит ею уже работающий сайт.
