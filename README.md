# ДЗ 3 — как запустить и что сдавать

## Что в папке

| Файл | Зачем |
|---|---|
| `docker-compose.yml` | MySQL 8.0 (volume, пароль из переменной, дамп в `/docker-entrypoint-initdb.d/`) + приложение FastAPI |
| `.env` | пароль root (`MYSQL_ROOT_PASSWORD`) |
| `Chinook_MySql_AutoIncrementPKs.sql` | дамп базы Chinook |
| `app/main.py` | приложение FastAPI (часть 2) — **сдаётся как скрипт** |
| `app/Dockerfile`, `app/requirements.txt` | сборка контейнера приложения |
| `hw3.ipynb` | тетрадка: задания 1.2 и 1.3 — **сдаётся** |
| `part3.md` | ответы на часть 3 |

## Где запускать без Docker на своём компьютере: GitHub Codespaces

1. Создать на github.com новый репозиторий (можно приватный), загрузить в него все файлы из папки, **сохранив структуру** (папка `app/` с тремя файлами внутри). Скрытый файл `.env` тоже нужен; если он не загрузился, создать его в Codespace командой ниже.
2. На странице репозитория: зелёная кнопка **Code → Codespaces → Create codespace on main**. Откроется VS Code в браузере, Docker там уже есть.
3. Дальше всё в терминале Codespace (внизу экрана).

## Шаги (и какие скриншоты делать)

```bash
cat .env || echo "MYSQL_ROOT_PASSWORD=chinook_root" > .env
```

### Скриншот 1 — задание 1.1: создание контейнера MySQL

```bash
docker compose up -d db
docker ps
docker volume ls
```

Подождать ~30–60 секунд (дамп загружается), пока в `docker ps` у `chinook-mysql` не станет `(healthy)`. Затем проверка, что база создалась:

```bash
docker exec chinook-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW TABLES FROM Chinook_AutoIncrement"'
```

### Тетрадка — задания 1.2 и 1.3

Открыть `hw3.ipynb` в Codespace, выбрать ядро Python (если VS Code предложит установить расширения Python/Jupyter или `ipykernel` — согласиться), нажать **Run All**. Сохранить тетрадку с выводами (Ctrl+S) и скачать её (правый клик по файлу → Download).

### (необязательно) Скриншот — данные переживают перезапуск благодаря volume

```bash
docker compose restart db
sleep 20
docker exec chinook-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT COUNT(*) FROM Chinook_AutoIncrement.customer_feedback"'
```

### Скриншот 2 — часть 2: docker compose

```bash
docker compose up -d --build
docker compose ps
```

### Скриншот 3 — часть 2: проверка эндпоинтов

```bash
curl -s "http://localhost:8000/api/tracks?genre=Jazz" | head -c 600; echo
curl -s http://localhost:8000/api/artists/1/albums; echo
curl -s http://localhost:8000/api/clients/1/report; echo
curl -s -X POST http://localhost:8000/api/feedback -H "Content-Type: application/json" -d '{"customer_id": 1, "track_id": 1, "rating": 5, "comment": "Great"}'; echo
curl -s -X POST http://localhost:8000/api/feedback -H "Content-Type: application/json" -d '{"customer_id": 1, "track_id": 1, "rating": 9}'; echo
```

Последний запрос специально невалидный — должен вернуть ошибку валидации (422).

Можно ещё открыть `/docs` (Swagger) через вкладку **Ports** → порт 8000 → адрес + `/docs`, и сделать скриншот.

## Что сдавать

1. Скриншоты 1–3 (и, по желанию, скриншот с перезапуском).
2. `hw3.ipynb` — с выполненными ячейками.
3. `app/main.py`.
4. Часть 3 — текст из `part3.md` (если нужно в тетрадке — вставить последней markdown-ячейкой).
5. Желательно приложить `docker-compose.yml` и `app/Dockerfile`, без них приложение не запустить.

После сдачи: Codespace можно остановить/удалить (github.com/codespaces), чтобы не тратить бесплатные часы.
