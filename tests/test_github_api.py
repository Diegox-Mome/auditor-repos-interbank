"""Pruebas de la capa de la API de GitHub SIN internet: una "sesión falsa" devuelve respuestas
con el mismo formato que documenta GitHub (árbol, cabeceras de rate limit, ETag, paginación)."""
import base64

import pytest

from auditor.github_api import (ApiBackend, AuthError, HttpClient, RateLimitError,
                                RepoAccessError, list_repos)
from auditor.models import FileEntry


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, content=b"", links=None, text=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.content = content
        self.links = links or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """Entrega respuestas en orden y guarda las peticiones que recibió."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.requests.append((url, params, headers))
        return self.responses.pop(0)


def make_http(responses, **kw):
    session = FakeSession(responses)
    sleeps = []
    http = HttpClient(token="tok", session_factory=lambda: session, sleep=sleeps.append,
                      now=lambda: 1000.0, **kw)
    return http, session, sleeps


TREE = {"sha": "T1", "truncated": False, "tree": [
    {"path": "pom.xml", "type": "blob", "sha": "a1"},
    {"path": "src", "type": "tree", "sha": "d1"},          # carpetas se ignoran
    {"path": "src/test/java/ATest.java", "type": "blob", "sha": "b2"},
    {"path": "vendor/lib", "type": "commit", "sha": "s1"},  # submódulos se ignoran
]}


def test_get_tree_parsea_y_guarda_etag(tmp_path):
    http, session, _ = make_http([FakeResponse(200, TREE, {"ETag": 'W/"abc"'})])
    tree = ApiBackend(http, tmp_path).get_tree("acme/app")
    assert [f.path for f in tree.files] == ["pom.xml", "src/test/java/ATest.java"]
    assert session.requests[0][1] == {"recursive": "1"}
    assert session.requests[0][2]["Authorization"] == "Bearer tok"


def test_segunda_llamada_usa_etag_y_acepta_304(tmp_path):
    http, session, _ = make_http([FakeResponse(200, TREE, {"ETag": 'W/"abc"'}), FakeResponse(304)])
    backend = ApiBackend(http, tmp_path)
    backend.get_tree("acme/app")
    tree = backend.get_tree("acme/app")                       # 304: sale del caché, sin gastar cuota
    assert session.requests[1][2]["If-None-Match"] == 'W/"abc"'
    assert len(tree.files) == 2


def test_repo_vacio_409(tmp_path):
    http, _, _ = make_http([FakeResponse(409)])
    assert ApiBackend(http, tmp_path).get_tree("acme/vacio").files == []


def test_repo_inexistente_o_sin_permiso(tmp_path):
    http, _, _ = make_http([FakeResponse(404), FakeResponse(404)])
    with pytest.raises(RepoAccessError):
        ApiBackend(http, tmp_path).get_tree("acme/privado")


def test_head_404_reintenta_con_rama_por_defecto(tmp_path):
    http, session, _ = make_http([FakeResponse(404), FakeResponse(200, {"default_branch": "develop"}),
                                  FakeResponse(200, TREE)])
    ApiBackend(http, tmp_path).get_tree("acme/app")
    assert session.requests[2][0].endswith("/git/trees/develop")


def test_rate_limit_espera_y_reintenta():
    limited = FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1030"})
    http, session, sleeps = make_http([limited, FakeResponse(200, {"ok": 1})])
    resp = http.get("https://api.github.com/x")
    assert resp.status_code == 200
    assert sleeps == [31.0]                                    # reset(1030) - ahora(1000) + 1


def test_rate_limit_demasiado_largo_falla_con_mensaje_claro():
    limited = FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999"})
    http, _, _ = make_http([limited], max_wait=900)
    with pytest.raises(RateLimitError, match="GITHUB_TOKEN"):
        http.get("https://api.github.com/x")


def test_403_por_permisos_no_se_confunde_con_rate_limit():
    http, _, sleeps = make_http([FakeResponse(403, headers={"X-RateLimit-Remaining": "4000"},
                                              text="Resource not accessible")])
    assert http.get("https://api.github.com/x").status_code == 403
    assert sleeps == []


def test_token_invalido():
    http, _, _ = make_http([FakeResponse(401)])
    with pytest.raises(AuthError):
        http.get("https://api.github.com/x")


def test_error_500_se_reintenta():
    http, _, sleeps = make_http([FakeResponse(502), FakeResponse(200, {})])
    assert http.get("https://api.github.com/x").status_code == 200
    assert sleeps == [1]


def test_lectura_por_raw_usa_cache_por_sha(tmp_path):
    http, session, _ = make_http([FakeResponse(200, content=b"<project/>")])
    backend = ApiBackend(http, tmp_path)
    entry = FileEntry("pom.xml", "a1")
    assert backend.read_file("acme/app", entry) == "<project/>"
    assert backend.read_file("acme/app", entry) == "<project/>"   # 2ª vez: del disco, sin petición
    assert len(session.requests) == 1
    assert session.requests[0][0] == "https://raw.githubusercontent.com/acme/app/HEAD/pom.xml"
    assert session.requests[0][2]["Authorization"] == "token tok"


def test_enterprise_server_lee_por_api_de_blobs(tmp_path):
    payload = {"encoding": "base64", "content": base64.b64encode(b"hola").decode()}
    session = FakeSession([FakeResponse(200, payload)])
    http = HttpClient(token="t", api_url="https://ghe.acme.com/api/v3", session_factory=lambda: session)
    backend = ApiBackend(http, tmp_path)
    assert backend.raw_url is None
    assert backend.read_file("acme/app", FileEntry("x.txt", "s9")) == "hola"
    assert session.requests[0][0] == "https://ghe.acme.com/api/v3/repos/acme/app/git/blobs/s9"


def test_listar_repos_pagina_y_filtra_archivados_y_forks():
    page1 = FakeResponse(200, [{"full_name": "o/a"}, {"full_name": "o/old", "archived": True}],
                         links={"next": {"url": "https://api.github.com/orgs/o/repos?page=2"}})
    page2 = FakeResponse(200, [{"full_name": "o/b"}, {"full_name": "o/fork", "fork": True}])
    http, _, _ = make_http([page1, page2])
    assert list_repos(http, "o") == ["o/a", "o/b"]


def test_listar_repos_prueba_usuario_si_no_es_organizacion():
    http, session, _ = make_http([FakeResponse(404), FakeResponse(200, [{"full_name": "u/x"}])])
    assert list_repos(http, "u") == ["u/x"]
    assert "/users/u/repos" in session.requests[1][0]
