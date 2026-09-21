"""Pruebas del motor. Sí: la herramienta que detecta pruebas tiene sus propias pruebas.

No usan internet: un "backend falso" entrega repos inventados (un diccionario ruta -> contenido).
Cada prueba describe un caso real de la vida: repo bien probado, repo con solo pruebas de
plantilla, repo de documentación, etc.
"""
from pathlib import Path

import pytest

from auditor.engine import Analyzer
from auditor.models import NA, NO, UNK, YES, FileEntry, Tree
from auditor.rules import load_rules

RULES = load_rules(Path(__file__).resolve().parent.parent / "rules.yaml")


class FakeBackend:
    def __init__(self, files: dict[str, str], truncated: bool = False):
        self.files = files
        self.truncated = truncated

    def get_tree(self, full_name):
        return Tree([FileEntry(p, f"sha-{i}") for i, p in enumerate(self.files)], self.truncated)

    def read_file(self, full_name, entry):
        return self.files[entry.path]


def analyze(files, deep=True, truncated=False):
    return Analyzer(RULES, FakeBackend(files, truncated), deep=deep).analyze("acme/repo")


# ------------------------------------------------------------------ Java
POM_JUNIT = "<dependency><artifactId>junit-jupiter</artifactId></dependency>"
JAVA_TEST = "class CalcTest { @Test void suma() {} @Test void resta() {} }"
CI_MVN = "steps:\n  - run: mvn -B test"


def test_java_bien_probado_confianza_alta():
    r = analyze({
        "pom.xml": POM_JUNIT,
        "src/main/java/Calc.java": "class Calc {}",
        "src/test/java/CalcTest.java": JAVA_TEST,
        ".github/workflows/ci.yml": CI_MVN,
    })
    assert (r.verdict, r.confidence) == (YES, "Alta")
    assert r.technologies == ["Java"]


def test_java_sin_ci_baja_a_confianza_media():
    r = analyze({"pom.xml": POM_JUNIT, "src/test/java/CalcTest.java": JAVA_TEST,
                 "src/main/java/A.java": "class A {}"})
    assert (r.verdict, r.confidence) == (YES, "Media")


def test_spring_initializr_solo_plantilla_no_cuenta():
    r = analyze({
        "pom.xml": "<artifactId>spring-boot-starter-test</artifactId>",
        "src/main/java/App.java": "class App {}",
        "src/test/java/AppTests.java": "class AppTests { @Test void contextLoads() {} }",
    })
    assert r.verdict == NO
    assert r.technologies == ["Java (Spring Boot)"]
    assert "plantilla" in r.evidence


def test_java_ci_con_skiptests_no_cuenta_como_ejecucion():
    r = analyze({"pom.xml": POM_JUNIT, "src/test/java/CalcTest.java": JAVA_TEST,
                 "src/main/java/A.java": "class A {}",
                 ".github/workflows/ci.yml": "run: mvn clean install -DskipTests"})
    assert r.findings[0].ci_runs_tests is False


def test_ci_comentado_no_cuenta():
    r = analyze({"pom.xml": POM_JUNIT, "src/test/java/CalcTest.java": JAVA_TEST,
                 "src/main/java/A.java": "class A {}",
                 ".github/workflows/ci.yml": "# run: mvn test"})
    assert r.findings[0].ci_runs_tests is False


def test_java_con_framework_pero_sin_archivos_es_no():
    r = analyze({"pom.xml": POM_JUNIT, "src/main/java/A.java": "class A {}"})
    assert r.verdict == NO
    assert "sin archivos de prueba" in r.evidence


def test_java_sin_nada_es_no_con_confianza_alta():
    r = analyze({"pom.xml": "<project/>", "src/main/java/A.java": "class A {}"})
    assert (r.verdict, r.confidence) == (NO, "Alta")


# --------------------------------------------------------------- Angular / JS
PKG_ANGULAR = '{"dependencies": {"@angular/core": "17"}, "devDependencies": {"karma": "6", "jasmine-core": "5"}}'


def test_angular_solo_spec_generado_es_no():
    r = analyze({
        "package.json": PKG_ANGULAR,
        "src/app/app.component.ts": "export class AppComponent {}",
        "src/app/app.component.spec.ts": "describe('AppComponent', () => {\n  it('should create the app', () => {});\n});",
    })
    assert r.verdict == NO
    assert r.technologies == ["Angular"]


def test_angular_con_pruebas_reales_es_si():
    r = analyze({
        "package.json": PKG_ANGULAR,
        "src/app/app.component.ts": "export class AppComponent {}",
        "src/app/calc.service.spec.ts": "describe('Calc', () => {\n  it('suma', () => {});\n  it('resta', () => {});\n});",
        ".github/workflows/ci.yml": "run: npm test -- --watch=false",
    })
    assert (r.verdict, r.confidence) == (YES, "Alta")
    assert r.technologies == ["Angular"]


def test_e2e_no_cuenta_como_unitaria():
    r = analyze({
        "package.json": '{"devDependencies": {"cypress": "13"}}',
        "src/index.js": "console.log(1)",
        "cypress/e2e/login.cy.js": "it('logs in', () => {})",
        "e2e/app.e2e-spec.ts": "it('works', () => {})",
    })
    assert r.verdict == NO


def test_npm_init_por_defecto_no_es_framework():
    r = analyze({
        "package.json": '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}',
        "index.js": "console.log(1)",
    })
    assert r.verdict == NO
    assert r.findings[0].declared is False


def test_regex_test_en_codigo_no_se_cuenta_como_caso():
    r = analyze({
        "package.json": '{"devDependencies": {"jest": "29"}}',
        "src/util.js": "x",
        "src/util.test.js": "const ok = /a/.test(name);",     # .test( de una regex, no un caso
    })
    assert r.verdict == NO


# --------------------------------------------------------------------- Python
def test_python_pytest_si():
    r = analyze({
        "pyproject.toml": "[tool.pytest.ini_options]",
        "app/main.py": "def f(): pass",
        "tests/test_main.py": "def test_f():\n    assert True\n",
        ".github/workflows/ci.yml": "run: pytest -q",
    })
    assert (r.verdict, r.confidence) == (YES, "Alta")


def test_python_carpeta_tests_vacia_es_no():
    r = analyze({"app/main.py": "def f(): pass", "tests/__init__.py": ""})
    assert r.verdict == NO


def test_python_pip_install_pytest_no_es_ejecutar():
    r = analyze({"app/main.py": "x", "tests/test_a.py": "def test_a(): pass",
                 ".github/workflows/ci.yml": "run: pip install pytest"})
    assert r.findings[0].ci_runs_tests is False


# ------------------------------------------------------------------- Go / .NET
def test_go_sin_dependencias_de_pruebas_igual_es_si():
    r = analyze({"go.mod": "module x", "main.go": "package main",
                 "main_test.go": "package main\nfunc TestMain(t *testing.T) {}\n"})
    assert r.verdict == YES


def test_dotnet_plantilla_xunit_no_cuenta():
    r = analyze({"src/App/App.csproj": "<Project/>", "src/App/Program.cs": "class P {}",
                 "tests/App.Tests/App.Tests.csproj": '<PackageReference Include="xunit" />',
                 "tests/App.Tests/UnitTest1.cs": "public class UnitTest1 { [Fact] public void Test1() {} }"})
    assert r.verdict == NO


def test_dotnet_con_pruebas_reales():
    r = analyze({"src/App/App.csproj": "<Project/>", "src/App/Program.cs": "class P {}",
                 "tests/App.Tests/App.Tests.csproj": '<PackageReference Include="xunit" />',
                 "tests/App.Tests/CalcTests.cs": "public class CalcTests { [Fact] public void Suma() {} }"})
    assert r.verdict == YES


# --------------------------------------------------------- casos de gobierno
def test_repo_de_documentacion_no_aplica():
    r = analyze({"README.md": "# docs", "docs/guia.md": "x", "terraform/main.tf": "x"})
    assert r.verdict == NA


def test_repo_vacio_no_aplica():
    assert analyze({}).verdict == NA


def test_lenguaje_sin_reglas_es_indeterminado():
    r = analyze({"lib/app.rb": "puts 1", "Gemfile": "x"})
    assert r.verdict == UNK
    assert r.technologies == ["Ruby"]


def test_monorepo_java_y_angular_una_tecnologia_con_pruebas_basta():
    r = analyze({
        "backend/pom.xml": POM_JUNIT, "backend/src/main/java/A.java": "class A {}",
        "backend/src/test/java/ATest.java": JAVA_TEST,
        "frontend/package.json": PKG_ANGULAR, "frontend/src/app/a.ts": "export class A {}",
    })
    assert r.verdict == YES
    assert set(r.technologies) == {"Java", "Angular"}
    assert {f.ecosystem: f.verdict for f in r.findings} == {"Java": YES, "JavaScript/TypeScript": NO}


def test_node_modules_se_ignora():
    r = analyze({"package.json": '{"devDependencies": {"jest": "29"}}', "src/a.js": "x",
                 "node_modules/lib/lib.test.js": "it('x', () => {})"})
    assert r.verdict == NO


def test_arbol_truncado_baja_la_confianza_de_un_no():
    r = analyze({"pom.xml": "<project/>", "src/main/java/A.java": "class A {}"}, truncated=True)
    assert (r.verdict, r.confidence) == (NO, "Baja")
    assert r.truncated


def test_modo_rapido_no_abre_archivos_pero_detecta_pruebas():
    r = analyze({"pom.xml": POM_JUNIT, "src/main/java/A.java": "class A {}",
                 "src/test/java/ATest.java": JAVA_TEST}, deep=False)
    assert r.verdict == YES
    assert r.findings[0].cases_real is None


def test_un_repo_roto_no_tumba_el_analisis():
    class Roto(FakeBackend):
        def get_tree(self, full_name):
            raise RuntimeError("boom")
    r = Analyzer(RULES, Roto({})).analyze("acme/x")
    assert r.verdict == "Error" and "boom" in r.evidence


@pytest.mark.parametrize("ext", [".rb", ".rs", ".swift"])
def test_varios_lenguajes_sin_reglas(ext):
    assert analyze({f"src/main{ext}": "x"}).verdict == UNK


# ------------------------------------------------- casos reales descubiertos en la demo
def test_laravel_solo_pruebas_de_ejemplo_es_no():
    r = analyze({
        "composer.json": '{"require-dev": {"phpunit/phpunit": "^11"}}',
        "app/Http/Controller.php": "<?php",
        "tests/Feature/ExampleTest.php": "<?php class ExampleTest { public function test_the_application_returns_a_successful_response(): void {} }",
        "tests/Unit/ExampleTest.php": "<?php class ExampleTest { public function test_that_true_is_true(): void {} }",
    })
    assert r.verdict == NO and "plantilla" in r.evidence


def test_make_test_cuenta_como_ejecucion_de_pruebas_en_go():
    r = analyze({"go.mod": "module x", "a.go": "package a",
                 "a_test.go": "func TestA(t *testing.T) {}",
                 ".github/workflows/go.yml": "      - name: Run Tests\n        run: make test"})
    assert r.findings[0].ci_runs_tests is True


# ------------------------------------------------------------ métricas de validación
def test_metricas_de_validacion():
    from auditor.validate import evaluate
    truth = {"a": "Sí", "b": "Sí", "c": "No", "d": "No"}
    pred = {"a": "Sí", "b": "No", "c": "Sí", "d": "No"}       # 1 acierto Sí, 1 FN, 1 FP, 1 acierto No
    res = evaluate(pred, truth)
    assert (res["tp"], res["fn"], res["fp"], res["tn"]) == (1, 1, 1, 1)
    assert res["precision"] == 0.5 and res["recall"] == 0.5 and res["accuracy"] == 0.5
    assert {e[0] for e in res["errors"]} == {"b", "c"}


def test_agregar_un_lenguaje_es_solo_datos(tmp_path):
    """Escalabilidad de lenguajes: Ruby se agrega con 12 líneas de YAML, sin tocar Python."""
    ruby = r"""
  ruby:
    name: Ruby
    manifests: [Gemfile]
    extensions: [.rb]
    fallback_by_extension: true
    framework_patterns: ['rspec', 'minitest']
    test_paths: ['(^|/)(spec|test)/.+\.rb$']
    case_patterns: ['^\s*(it|specify)\s+[''"]', '^\s*def\s+test_\w+']
    ci_patterns: ['\brspec\b', '\brake\s+test\b']
"""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text((Path(__file__).resolve().parent.parent / "rules.yaml").read_text(encoding="utf-8") + ruby,
                          encoding="utf-8")
    files = {"Gemfile": "gem 'rspec'", "lib/app.rb": "class App; end",
             "spec/app_spec.rb": "describe App do\n  it 'works' do\n  end\nend\n"}
    r = Analyzer(load_rules(rules_file), FakeBackend(files)).analyze("acme/rb")
    assert r.verdict == YES and r.technologies == ["Ruby"]


def test_confianza_de_un_no_es_la_del_eslabon_mas_debil():
    r = analyze({
        "backend/pom.xml": "<project/>", "backend/src/main/java/A.java": "class A {}",   # Java: No, Alta
        "composer.json": '{"require-dev": {"phpunit/phpunit": "^11"}}', "web/x.php": "<?php",
        "tests/Unit/ExampleTest.php": "<?php class E { public function test_that_true_is_true(): void {} }",  # PHP: No, Media
    })
    assert r.verdict == NO and r.confidence == "Media"
