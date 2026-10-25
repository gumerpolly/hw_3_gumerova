# Часть 3. Ошибки и спорные решения в коде

## 1. SQL-инъекции (конкатенация строк и f-строки в SQL)

**Что не так.** Запросы собираются через `+ str(customer_id)` и f-строки: `f"UPDATE ... {amount} ... {from_id}"`, `f"... LIMIT {limit} OFFSET ..."`.

**Почему это проблема.** В `/api/transfer` значения `amount`, `from_customer_id`, `to_customer_id` приходят из JSON без проверок, а в `/api/tracks` `limit` и `page` — из строки запроса. Злоумышленник может передать, например, `"to_customer_id": "1 OR 1=1"` и изменить баланс всех клиентов, или `limit=1 UNION SELECT ...` и вытащить чужие данные. В `get_customer` сейчас спасает конвертер `<int:...>`, но сама функция небезопасна: её легко вызвать из другого места со строкой.

**Как исправить.** Только параметризованные запросы с плейсхолдерами:
`cur.execute("SELECT ... WHERE CustomerId = %s", (customer_id,))`.
Для `LIMIT/OFFSET` — тоже `%s`, предварительно приведя значения к `int`.

## 2. Учётные данные захардкожены, подключение под root с пустым паролем

**Что не так.** `user="root", password=""` прописаны в коде трижды.

**Почему это проблема.** Пароль попадает в git; приложение работает с максимальными правами (может удалить всю БД); пустой пароль root — дыра в безопасности; чтобы сменить пароль, нужно править и пересобирать код.

**Как исправить.** Читать параметры из переменных окружения (`os.environ["DB_PASSWORD"]`) или секретов; создать отдельного пользователя БД с минимально нужными правами (`SELECT`, `UPDATE` на нужные таблицы).

## 3. `host="localhost"` и имя базы

**Что не так.** Хост `localhost`, база `chinook`.

**Почему это проблема.** Внутри Docker `localhost` — это сам контейнер приложения, а не контейнер MySQL, подключение не пройдёт. База из дампа называется `Chinook_AutoIncrement`, а на Linux имена баз и таблиц в MySQL чувствительны к регистру, так что `chinook` не найдётся.

**Как исправить.** Хост и имя БД — из конфигурации (`DB_HOST=db`, имя сервиса в docker-compose; `DB_NAME=Chinook_AutoIncrement`).

## 4. Дублирование кода подключения и новое соединение на каждый запрос

**Что не так.** Один и тот же `mysql.connector.connect(...)` скопирован в три функции, соединение открывается заново на каждый HTTP-запрос.

**Почему это проблема.** Изменение настроек надо вносить в нескольких местах; установка соединения дорогая, под нагрузкой можно упереться в `max_connections`.

**Как исправить.** Вынести в одну функцию `get_connection()` и использовать пул соединений (`mysql.connector.pooling.MySQLConnectionPool`) или ORM/SQLAlchemy с пулом.

## 5. Утечка соединений и курсоров

**Что не так.** В `get_customer` и `get_tracks` соединение и курсор не закрываются вообще; в `transfer` `con.close()` не вызовется, если запрос упадёт с исключением.

**Почему это проблема.** Каждое обращение оставляет открытое соединение, со временем MySQL отвечает `Too many connections` и приложение перестаёт работать.

**Как исправить.** `try/finally` или контекстные менеджеры: закрывать курсор и соединение (возвращать в пул) в любом случае.

## 6. Нет обработки ошибок и проверки входных данных

**Что не так.** `request.json` может быть `None` (не тот Content-Type) → `TypeError`; отсутствующий ключ → `KeyError`; ошибки БД не перехватываются.

**Почему это проблема.** Клиент получает `500 Internal Server Error` вместо понятного `400 Bad Request`; при включённом debug наружу утекает трейсбек с деталями устройства системы.

**Как исправить.** Валидировать тело запроса (`request.get_json(silent=True)`, проверка ключей и типов, либо схема Pydantic/Marshmallow), перехватывать `mysql.connector.Error`, возвращать корректные коды (400, 404, 409, 500) и понятное сообщение.

## 7. `/api/transfer`: в таблице `Customer` нет столбца `Balance`

**Что не так.** В схеме Chinook у `Customer` нет поля `Balance`.

**Почему это проблема.** Эндпоинт всегда падает с ошибкой `Unknown column 'Balance'`.

**Как исправить.** Добавить столбец миграцией (`ALTER TABLE Customer ADD COLUMN Balance DECIMAL(10,2) NOT NULL DEFAULT 0`) или хранить балансы в отдельной таблице счетов.

## 8. `/api/transfer`: нет нормальной транзакции и отката

**Что не так.** Два `UPDATE` выполняются без явного `start_transaction()` и без `rollback()` при ошибке.

**Почему это проблема.** Перевод должен быть атомарным: либо списание и зачисление вместе, либо ничего. Сейчас при ошибке на втором запросе нет явного отката, а соединение с незавершённой транзакцией просто бросается (и держит блокировки до таймаута).

**Как исправить.**
```python
con.start_transaction()
try:
    ...два UPDATE...
    con.commit()
except Exception:
    con.rollback()
    raise
```

## 9. `/api/transfer`: нет бизнес-проверок

**Что не так.** Не проверяется, что:
- `amount > 0` (отрицательная сумма = перевод в обратную сторону, «кража»);
- `from_id != to_id`;
- оба клиента существуют (`UPDATE` несуществующего id «успешно» меняет 0 строк — деньги списались в никуда или появились из ниоткуда);
- на счёте хватает средств.

**Почему это проблема.** Нарушается целостность данных, деньги создаются или исчезают.

**Как исправить.** Валидировать сумму; списывать условием `UPDATE ... SET Balance = Balance - %s WHERE CustomerId = %s AND Balance >= %s` и проверять `cur.rowcount == 1` для обоих запросов, иначе `rollback` и ошибка 400/404/409.

## 10. `/api/transfer`: гонки и взаимоблокировки

**Что не так.** Проверка баланса (если её добавить отдельным `SELECT`) и обновление не атомарны; два встречных перевода A→B и B→A блокируют строки в разном порядке.

**Почему это проблема.** Возможны двойное списание при параллельных запросах и deadlock.

**Как исправить.** Блокировать строки `SELECT ... FOR UPDATE` в одной транзакции, всегда в одном порядке (например, по возрастанию `CustomerId`); либо использовать условный `UPDATE` из п. 9. Обрабатывать ошибку deadlock повтором транзакции.

## 11. `/api/transfer`: деньги во float, нет аутентификации, ответ всегда «success»

**Что не так.** `amount` из JSON — `float`; любой может вызвать перевод; ответ `{"status": "success"}` возвращается, даже если ничего не изменилось.

**Почему это проблема.** Ошибки округления (`0.1 + 0.2 != 0.3`); переводить чужие деньги может кто угодно; клиент введён в заблуждение.

**Как исправить.** `Decimal` и `DECIMAL(10,2)` в БД; аутентификация и проверка, что пользователь распоряжается счётом `from_id`; возвращать реальный результат и корректный код (например, 200 с данными операции или 4xx при отказе); логировать операции.

## 12. `/api/tracks`: `page` и `limit` — строки

**Что не так.** `request.args.get('page', 1)` возвращает строку, если параметр передан.

**Почему это проблема.** `page * limit` для `?page=2` даёт `"2" * 10 = "2222222222"` (повторение строки), для `?page=2&limit=5` — `TypeError` и 500. В SQL подставляется мусор.

**Как исправить.** `request.args.get('page', 1, type=int)`, `request.args.get('limit', 10, type=int)`.

## 13. `/api/tracks`: неверный OFFSET

**Что не так.** `OFFSET page * limit`, нумерация страниц с 1.

**Почему это проблема.** Первая страница (`page=1`) пропускает первые `limit` треков — их невозможно получить.

**Как исправить.** `offset = (page - 1) * limit`.

## 14. `/api/tracks`: нет ограничений на параметры

**Что не так.** `limit` и `page` не проверяются.

**Почему это проблема.** `limit=1000000` выгружает всю таблицу (нагрузка, DoS); отрицательные значения дают ошибку SQL и 500.

**Как исправить.** Проверять `page >= 1`, `1 <= limit <= 100` (максимум задать константой), иначе 400.

## 15. `/api/tracks`: пагинация без ORDER BY

**Что не так.** `SELECT * FROM Track LIMIT ... OFFSET ...` без сортировки.

**Почему это проблема.** Порядок строк без `ORDER BY` не гарантирован: записи могут повторяться или теряться между страницами.

**Как исправить.** `ORDER BY TrackId` (по уникальному ключу); для больших таблиц — keyset-пагинация (`WHERE TrackId > %s ORDER BY TrackId LIMIT %s`).

## 16. `SELECT *` и ответ без имён полей

**Что не так.** `SELECT *` и `jsonify(cur.fetchall())` возвращают список массивов вида `[1, "Luís", "Gonçalves", ...]`.

**Почему это проблема.** Клиенту непонятно, какое значение что означает; при изменении схемы (новый столбец) меняется порядок и формат API; наружу уходят все поля, включая персональные данные (телефон, адрес, факс) — лишняя утечка ПДн.

**Как исправить.** Явно перечислять нужные столбцы, использовать `cursor(dictionary=True)` и возвращать объекты с ключами (`{"customer_id": 1, "first_name": ...}`).

## 17. `get_customer`: нет 404 и `fetchall` для одной записи

**Что не так.** Для несуществующего клиента возвращается `[]` с кодом 200; ищется одна запись, но используется `fetchall()`.

**Почему это проблема.** Клиент API не может отличить «нет такого клиента» от успешного ответа.

**Как исправить.** `row = cur.fetchone()`; если `None` — `abort(404)`, иначе вернуть объект.

## 18. Смешение слоёв

**Что не так.** Функция доступа к данным `get_customer` сама вызывает `jsonify` и формирует HTTP-ответ.

**Почему это проблема.** Функцию нельзя переиспользовать вне Flask-запроса и сложно тестировать.

**Как исправить.** Слой работы с БД возвращает данные (dict/модель), а эндпоинт превращает их в HTTP-ответ.

## 19. Прочее

- Нет логирования ошибок и операций (особенно переводов денег).
- Нет точки запуска / конфигурации сервера (`if __name__ == "__main__": app.run(...)` или запуск через gunicorn); для продакшена нельзя использовать встроенный dev-сервер и `debug=True`.
- По условию ДЗ приложение должно быть на FastAPI; там валидация параметров и тела запроса (Pydantic) решает пп. 6, 12, 14 «из коробки».

## Исправленный вариант (Flask)

```python
import os
from decimal import Decimal, InvalidOperation

from flask import Flask, jsonify, request, abort
import mysql.connector
from mysql.connector import pooling

app = Flask(__name__)

pool = pooling.MySQLConnectionPool(
    pool_name="chinook",
    pool_size=5,
    host=os.environ.get("DB_HOST", "db"),
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    database=os.environ.get("DB_NAME", "Chinook_AutoIncrement"),
)

MAX_LIMIT = 100


def fetch_customer(customer_id):
    con = pool.get_connection()
    try:
        cur = con.cursor(dictionary=True)
        cur.execute(
            "SELECT CustomerId, FirstName, LastName, Email FROM Customer WHERE CustomerId = %s",
            (customer_id,),
        )
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        con.close()


@app.route("/api/customers/<int:customer_id>")
def customer_endpoint(customer_id):
    customer = fetch_customer(customer_id)
    if customer is None:
        abort(404, description="Customer not found")
    return jsonify(customer)


@app.route("/api/transfer", methods=["POST"])
def transfer():
    data = request.get_json(silent=True) or {}
    try:
        from_id = int(data["from_customer_id"])
        to_id = int(data["to_customer_id"])
        amount = Decimal(str(data["amount"]))
    except (KeyError, ValueError, TypeError, InvalidOperation):
        abort(400, description="from_customer_id, to_customer_id and amount are required")
    if amount <= 0 or from_id == to_id:
        abort(400, description="Invalid amount or customers")

    con = pool.get_connection()
    try:
        con.start_transaction()
        cur = con.cursor()
        first, second = sorted([from_id, to_id])
        cur.execute(
            "SELECT CustomerId FROM Customer WHERE CustomerId IN (%s, %s) ORDER BY CustomerId FOR UPDATE",
            (first, second),
        )
        if len(cur.fetchall()) != 2:
            con.rollback()
            abort(404, description="Customer not found")
        cur.execute(
            "UPDATE Customer SET Balance = Balance - %s WHERE CustomerId = %s AND Balance >= %s",
            (amount, from_id, amount),
        )
        if cur.rowcount != 1:
            con.rollback()
            abort(409, description="Insufficient funds")
        cur.execute(
            "UPDATE Customer SET Balance = Balance + %s WHERE CustomerId = %s",
            (amount, to_id),
        )
        con.commit()
        cur.close()
    except mysql.connector.Error:
        con.rollback()
        abort(500, description="Database error")
    finally:
        con.close()
    return jsonify({"status": "success", "from": from_id, "to": to_id, "amount": str(amount)})


@app.route("/api/tracks")
def get_tracks():
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 10, type=int)
    if page < 1 or not 1 <= limit <= MAX_LIMIT:
        abort(400, description=f"page >= 1, 1 <= limit <= {MAX_LIMIT}")

    con = pool.get_connection()
    try:
        cur = con.cursor(dictionary=True)
        cur.execute(
            "SELECT TrackId, Name, AlbumId, GenreId, UnitPrice FROM Track "
            "ORDER BY TrackId LIMIT %s OFFSET %s",
            (limit, (page - 1) * limit),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        con.close()
    return jsonify({"page": page, "limit": limit, "tracks": rows})
```

(Для `/api/transfer` предполагается, что столбец `Balance DECIMAL(10,2)` добавлен миграцией, а аутентификация вынесена в отдельный слой.)
