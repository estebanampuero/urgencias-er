"""
Búsqueda semántica de notas clínicas históricas usando embeddings.

Implementación sin dependencias pesadas: TF-IDF + coseno como baseline
funcional desde el día 1. Si más adelante hay >5k notas, hacer drop-in
upgrade a faster-whisper compatible encoder o sentence-transformers.

Almacenamiento: tabla `notas_idx` con vectores precomputados (BLOB JSON).
Reconstruir cuando hay cambios significativos vía `rebuild_index()`.

API:
  buscar(query, limit=20) -> [{paciente_id, nota_id, contenido, score, ...}]
"""
import json
import math
import re
from collections import Counter
from typing import Iterator

from database import get_conn

_STOPWORDS = {
    "el","la","los","las","de","del","y","a","en","un","una","que","se","con",
    "por","para","es","al","lo","como","mas","más","pero","sus","le","ya","o",
    "este","esta","estos","estas","ese","esa","esos","esas","fue","ser","sin",
}

_TOK_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{3,}")


def _tokenize(texto: str) -> list[str]:
    return [w.lower() for w in _TOK_RE.findall(texto or "") if w.lower() not in _STOPWORDS]


def _tf(tokens: list[str]) -> dict[str, float]:
    if not tokens:
        return {}
    c = Counter(tokens)
    n = len(tokens)
    return {w: c[w] / n for w in c}


def _idf(documentos: list[list[str]]) -> dict[str, float]:
    N = len(documentos)
    df = Counter()
    for doc in documentos:
        for w in set(doc):
            df[w] += 1
    return {w: math.log(N / (1 + df[w])) for w in df}


def _vector(tf: dict[str, float], idf: dict[str, float]) -> dict[str, float]:
    return {w: tf[w] * idf.get(w, 0.0) for w in tf}


def _coseno(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    # producto punto sobre las claves intersección
    keys = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(v*v for v in a.values()))
    nb = math.sqrt(sum(v*v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _ensure_index_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS notas_idx (
            nota_id INTEGER PRIMARY KEY,
            vector TEXT NOT NULL,
            tokens TEXT NOT NULL,
            FOREIGN KEY (nota_id) REFERENCES notas(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS busqueda_idf (
            termino TEXT PRIMARY KEY,
            idf REAL NOT NULL
        );
    """)


def rebuild_index() -> dict:
    """Recalcula IDF + vectores de todas las notas. Llamar cuando se inserten
    muchas notas o periódicamente (e.g. al cerrar turno)."""
    conn = get_conn()
    _ensure_index_table(conn)
    notas = conn.execute("SELECT id, contenido FROM notas").fetchall()
    if not notas:
        conn.close()
        return {"ok": True, "n": 0}
    docs = [_tokenize(n["contenido"]) for n in notas]
    idf = _idf(docs)
    # persistir IDF
    conn.execute("DELETE FROM busqueda_idf")
    conn.executemany(
        "INSERT INTO busqueda_idf (termino, idf) VALUES (?, ?)",
        list(idf.items()),
    )
    # persistir vectores
    conn.execute("DELETE FROM notas_idx")
    for n, toks in zip(notas, docs):
        v = _vector(_tf(toks), idf)
        conn.execute(
            "INSERT INTO notas_idx (nota_id, vector, tokens) VALUES (?, ?, ?)",
            (n["id"], json.dumps(v), json.dumps(toks)),
        )
    conn.commit()
    conn.close()
    return {"ok": True, "n": len(notas), "vocab": len(idf)}


def index_nota(nota_id: int, contenido: str) -> dict:
    """
    Indexa una nota nueva incrementalmente reutilizando el IDF ya calculado.
    Si no hay índice, llama a rebuild_index() (cold start).

    El IDF se mantiene fijo entre rebuilds; con el tiempo pierde precisión para
    términos nuevos. Llamar a rebuild_index() periódicamente para refrescar.
    """
    conn = get_conn()
    _ensure_index_table(conn)
    idf_rows = conn.execute("SELECT termino, idf FROM busqueda_idf").fetchall()
    if not idf_rows:
        conn.close()
        return rebuild_index()
    idf = {r["termino"]: r["idf"] for r in idf_rows}
    toks = _tokenize(contenido)
    v = _vector(_tf(toks), idf)
    conn.execute(
        "INSERT OR REPLACE INTO notas_idx (nota_id, vector, tokens) VALUES (?, ?, ?)",
        (nota_id, json.dumps(v), json.dumps(toks)),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "n": 1, "incremental": True}


def buscar(query: str, limit: int = 20) -> list[dict]:
    """Búsqueda semántica de notas por similitud coseno TF-IDF."""
    conn = get_conn()
    _ensure_index_table(conn)
    if not conn.execute("SELECT 1 FROM notas_idx LIMIT 1").fetchone():
        # primer uso: construir índice
        conn.close()
        rebuild_index()
        conn = get_conn()

    idf_rows = conn.execute("SELECT termino, idf FROM busqueda_idf").fetchall()
    idf = {r["termino"]: r["idf"] for r in idf_rows}
    q_toks = _tokenize(query)
    if not q_toks:
        conn.close()
        return []
    q_vec = _vector(_tf(q_toks), idf)

    # Escaneo lineal (OK hasta ~5-10k notas; para más, usar pgvector/sqlite-vec)
    resultados = []
    rows = conn.execute(
        """SELECT ni.nota_id, ni.vector, n.contenido, n.paciente_id, n.creado_en,
                  p.nombre AS paciente_nombre, p.categoria_esi
           FROM notas_idx ni
           JOIN notas n ON n.id = ni.nota_id
           JOIN pacientes p ON p.id = n.paciente_id"""
    ).fetchall()
    for r in rows:
        v = json.loads(r["vector"])
        score = _coseno(q_vec, v)
        if score > 0.05:
            resultados.append({
                "nota_id": r["nota_id"],
                "paciente_id": r["paciente_id"],
                "paciente_nombre": r["paciente_nombre"],
                "categoria_esi": r["categoria_esi"],
                "contenido": r["contenido"],
                "creado_en": r["creado_en"],
                "score": round(score, 4),
            })
    conn.close()
    resultados.sort(key=lambda x: x["score"], reverse=True)
    return resultados[:limit]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "rebuild":
        r = rebuild_index()
        print(f"Índice reconstruido: {r}")
    else:
        q = " ".join(sys.argv[1:]) or "dolor torácico"
        print(f"Buscando: {q!r}")
        for r in buscar(q, limit=5):
            print(f"  [{r['score']:.3f}] {r['categoria_esi']} {r['paciente_nombre']}: {r['contenido'][:80]}...")
