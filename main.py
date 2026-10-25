import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import mysql.connector
from mysql.connector import errorcode, pooling
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "db"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "Chinook_AutoIncrement"),
}

CREATE_FEEDBACK_TABLE = """
CREATE TABLE IF NOT EXISTS customer_feedback (
    feedback_id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    track_id INT NOT NULL,
    rating INT NOT NULL,
    comment TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_feedback_customer FOREIGN KEY (customer_id) REFERENCES Customer (CustomerId),
    CONSTRAINT fk_feedback_track FOREIGN KEY (track_id) REFERENCES Track (TrackId),
    CONSTRAINT chk_feedback_rating CHECK (rating BETWEEN 1 AND 5)
)
"""

pool: Optional[pooling.MySQLConnectionPool] = None


def create_pool(retries: int = 30, delay: float = 2.0) -> pooling.MySQLConnectionPool:
    last_error = None
    for _ in range(retries):
        try:
            return pooling.MySQLConnectionPool(pool_name="chinook", pool_size=5, **DB_CONFIG)
        except mysql.connector.Error as e:
            last_error = e
            time.sleep(delay)
    raise RuntimeError(f"Cannot connect to MySQL: {last_error}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = create_pool()
    con = pool.get_connection()
    try:
        cur = con.cursor()
        cur.execute(CREATE_FEEDBACK_TABLE)
        con.commit()
        cur.close()
    finally:
        con.close()
    yield


app = FastAPI(title="Chinook API", lifespan=lifespan)


def get_db():
    con = pool.get_connection()
    try:
        yield con
    finally:
        con.close()


def fetch_all(con, sql: str, params: tuple = ()) -> list[dict]:
    cur = con.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def fetch_one(con, sql: str, params: tuple = ()) -> Optional[dict]:
    rows = fetch_all(con, sql, params)
    return rows[0] if rows else None


@app.get("/api/tracks")
def tracks_by_genre(genre: str = Query(..., min_length=1), con=Depends(get_db)):
    sql = """
        SELECT t.TrackId, t.Name AS Track, al.Title AS Album, ar.Name AS Artist,
               g.Name AS Genre, t.Composer, t.Milliseconds, t.UnitPrice
        FROM Track t
        JOIN Genre g ON g.GenreId = t.GenreId
        LEFT JOIN Album al ON al.AlbumId = t.AlbumId
        LEFT JOIN Artist ar ON ar.ArtistId = al.ArtistId
        WHERE g.Name = %s
        ORDER BY t.TrackId
    """
    rows = fetch_all(con, sql, (genre,))
    return {"genre": genre, "count": len(rows), "tracks": rows}


@app.get("/api/artists/{artist_id}/albums")
def artist_albums(artist_id: int, con=Depends(get_db)):
    artist = fetch_one(con, "SELECT ArtistId, Name FROM Artist WHERE ArtistId = %s", (artist_id,))
    if artist is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    albums = fetch_all(
        con,
        "SELECT AlbumId, Title FROM Album WHERE ArtistId = %s ORDER BY Title",
        (artist_id,),
    )
    return {"artist": artist, "albums": albums}


@app.get("/api/clients/{client_id}/report")
def client_report(client_id: int, con=Depends(get_db)):
    sql = """
        SELECT c.CustomerId, c.FirstName, c.LastName, c.Email,
               (SELECT COUNT(*) FROM Invoice i WHERE i.CustomerId = c.CustomerId) AS InvoiceCount,
               (SELECT COALESCE(SUM(i.Total), 0) FROM Invoice i WHERE i.CustomerId = c.CustomerId) AS TotalSpent,
               (SELECT g.Name
                FROM Invoice i
                JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
                JOIN Track t ON t.TrackId = il.TrackId
                JOIN Genre g ON g.GenreId = t.GenreId
                WHERE i.CustomerId = c.CustomerId
                GROUP BY g.GenreId, g.Name
                ORDER BY SUM(il.Quantity) DESC, g.Name
                LIMIT 1) AS TopGenre
        FROM Customer c
        WHERE c.CustomerId = %s
    """
    report = fetch_one(con, sql, (client_id,))
    if report is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return report


class FeedbackIn(BaseModel):
    customer_id: int
    track_id: int
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


@app.post("/api/feedback", status_code=201)
def add_feedback(feedback: FeedbackIn, con=Depends(get_db)):
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO customer_feedback (customer_id, track_id, rating, comment) "
            "VALUES (%s, %s, %s, %s)",
            (feedback.customer_id, feedback.track_id, feedback.rating, feedback.comment),
        )
        con.commit()
        feedback_id = cur.lastrowid
    except mysql.connector.IntegrityError as e:
        con.rollback()
        if e.errno == errorcode.ER_NO_REFERENCED_ROW_2:
            raise HTTPException(status_code=400, detail="Customer or track does not exist")
        raise HTTPException(status_code=400, detail=str(e.msg))
    except mysql.connector.Error:
        con.rollback()
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        cur.close()

    return fetch_one(
        con,
        "SELECT feedback_id, customer_id, track_id, rating, comment, created_at "
        "FROM customer_feedback WHERE feedback_id = %s",
        (feedback_id,),
    )
