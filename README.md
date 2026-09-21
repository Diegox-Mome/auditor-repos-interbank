# 🛡️ Repo Test Auditor (Enterprise Edition)

**Repo Test Auditor** es una herramienta automatizada de análisis estático diseñada para auditar repositorios de GitHub a gran escala. Su objetivo es detectar la existencia, madurez y validez de las pruebas unitarias en múltiples lenguajes de programación, reduciendo de forma inteligente los falsos positivos generados por herramientas de *scaffolding* y plantillas autogeneradas.

El motor fue diseñado bajo principios de gobierno de repositorios y automatización, proporcionando la escalabilidad necesaria para auditar ecosistemas corporativos de más de 2,000 repositorios sin comprometer el rendimiento de la red.

---

## 🚀 Características y decisiones de arquitectura

El diseño de esta herramienta prioriza la eficiencia, la precisión y la extensibilidad mediante cuatro pilares fundamentales:

1. **Análisis sin clonación completa (Blobless/Tree API):** En entornos empresariales, ejecutar `git clone` sobre miles de repositorios puede saturar el almacenamiento y el ancho de banda. El motor se conecta a la API de GitHub o utiliza clones *blobless* para extraer únicamente el árbol de archivos (*Git Trees*) y el contenido estrictamente necesario para el análisis.

2. **Sistema anti falsos positivos:** La simple existencia de un archivo como `*Test.java` o `*.spec.ts` no garantiza la calidad de las pruebas. El motor analiza semánticamente una muestra de los archivos identificados para descartar casos triviales o generados automáticamente, como `contextLoads()` de Spring Boot o `should create` de Angular CLI. Si un repositorio solo contiene pruebas autogeneradas, se clasifica como carente de pruebas reales.

3. **Diseño *data-driven* y agnóstico al lenguaje:** El núcleo del motor está completamente desacoplado de las reglas de negocio. Las reglas de detección para cada lenguaje —manifiestos, expresiones regulares, patrones de archivos y comandos de CI— residen en un archivo declarativo llamado `rules.yaml`. Esto permite incorporar nuevas tecnologías sin modificar el código fuente.

4. **Escalabilidad y manejo de límites de red:**
   - **Procesamiento paralelo:** La opción `--workers` permite ejecutar análisis concurrentes para maximizar el rendimiento.
   - **Control de *rate limits*:** El cliente HTTP intercepta respuestas `403` y `429`, calcula el valor de `X-RateLimit-Reset` y pausa la ejecución de forma automática hasta que la cuota vuelva a estar disponible.
   - **Caché basada en ETag/SHA:** El motor utiliza una caché transaccional para omitir descargas redundantes en ejecuciones recurrentes.

---

## 🛠️ Instalación y configuración

### 1. Clonar el repositorio

```bash
git clone [https://github.com/Diegox-Mome/auditor-repos-interbank.git](https://github.com/Diegox-Mome/auditor-repos-interbank.git)
cd auditor-repos-interbank
```

### 2. Instalar dependencias

Se requiere Python 3.10 o una versión superior.

```bash
pip install -r requirements.txt
```

### 3. Configurar la autenticación

Para analizar repositorios privados o ejecutar auditorías masivas sin enfrentar límites restrictivos de cuota, se recomienda configurar un **Personal Access Token (PAT)** de GitHub con permisos de lectura.

#### Windows PowerShell

```powershell
$env:GITHUB_TOKEN = "ghp_tu_token_aqui"
```

#### Linux y macOS

```bash
export GITHUB_TOKEN="ghp_tu_token_aqui"
```

> **Nota:** El token se envía exclusivamente mediante la cabecera HTTP de autorización. No se expone en la línea de comandos ni se almacena en el código fuente.

---

## 💻 Uso de la herramienta

La interfaz de línea de comandos (CLI) permite ejecutar auditorías en diferentes modalidades.

### Modo demo: lote de muestra

Analiza una lista de repositorios especificados en un archivo de texto mediante el backend de la API REST:

```bash
python -m auditor --repos-file samples/demo_repos.txt --backend api --workers 6
```

### Modo corporativo: auditoría organizacional

Analiza todos los repositorios pertenecientes a una organización de GitHub:

```bash
python -m auditor --org nombre_organizacion --workers 8
```

### Compatibilidad con GitHub Enterprise Server

Para analizar repositorios alojados en una instancia *on-premise* de GitHub Enterprise Server:

```bash
python -m auditor \
  --org nombre_organizacion \
  --api-url [https://ghe.miempresa.com/api/v3](https://ghe.miempresa.com/api/v3)
```

### Reanudación de una auditoría interrumpida

Si una ejecución se interrumpe, el análisis puede reanudarse utilizando el checkpoint transaccional generado previamente:

```bash
python -m auditor --org nombre_organizacion --resume
```

---

## 🧠 Flujo de evaluación de confianza

Para cada repositorio auditado, el motor evalúa tres capas independientes de evidencia con el objetivo de asignar un **nivel de confianza** —alto, medio o bajo— al veredicto final.

1. **Evidencia declarada:** Verifica si el framework de pruebas, como `junit` o `jest`, está configurado en las dependencias del proyecto, por ejemplo, en `pom.xml`, `package.json` u otros manifiestos compatibles.

2. **Evidencia presente:** Comprueba si existen directorios o archivos que cumplen con los patrones de nomenclatura de pruebas unitarias y si contienen aserciones o casos de prueba reales.

3. **Evidencia ejecutada:** Analiza si el pipeline de Integración Continua (CI/CD) contiene comandos que automatizan la ejecución de las pruebas identificadas.

Cuando las tres capas convergen, el repositorio recibe un nivel de confianza **alto**. Las inconsistencias —por ejemplo, dependencias de pruebas declaradas, pero ningún archivo de prueba presente— reducen el nivel de confianza y permiten identificar repositorios que requieren revisión manual o refactorización.

---

## 🧩 Extensibilidad: incorporación de nuevas tecnologías

La arquitectura permite incorporar nuevos lenguajes y frameworks con un esfuerzo mínimo. Para agregar, por ejemplo, soporte para **Ruby**, solo es necesario añadir el siguiente bloque declarativo al archivo `rules.yaml`:

```yaml
ruby:
  name: Ruby
  manifests:
    - Gemfile
  extensions:
    - .rb
  fallback_by_extension: true
  framework_patterns:
    - rspec
    - minitest
  test_paths:
    - '(^|/)(spec|test)/.+\.rb$'
  case_patterns:
    - '^\s*(it|specify)\s+[''"]'
    - '^\s*def\s+test_\w+'
  ci_patterns:
    - '\brspec\b'
    - '\brake\s+test\b'
```

Este enfoque permite centralizar la lógica de detección en archivos de configuración y mantener el código principal estable, reutilizable y fácil de mantener.

---

## 📊 Salida de datos y reportería

Al finalizar el análisis, el motor genera los resultados en el directorio `output/`:

- **`results.csv`**: Tabla consolidada con el listado de repositorios, la tecnología principal detectada, el veredicto booleano de calidad y el nivel de confianza asignado.

- **`detail.csv`**: Desglose detallado de las tres capas de evidencia: declarada, presente y ejecutada. Este archivo está orientado a auditorías técnicas y revisiones profundas.

- **`summary.md`**: Documento en formato Markdown con los principales *insights* ejecutivos, las métricas de cobertura y el estado general de la organización auditada.

- **`results.jsonl`**: Checkpoint transaccional que permite reanudar el análisis mediante la opción `--resume` en caso de interrupción.

---

## 🧪 Testing y validación continua

La herramienta incluye una suite de pruebas automatizadas basada en un enfoque *Test-Driven Development* (TDD), cuyo objetivo es garantizar la integridad del propio motor. La suite cubre 48 escenarios, incluyendo validaciones de reglas, procesamiento de repositorios y respuestas de la API simuladas mediante *mocks*.

Para ejecutar las pruebas:

```bash
python -m pytest -q
```

Adicionalmente, el proyecto proporciona el módulo `auditor.validate`, que permite comparar los resultados predictivos del motor con un conjunto de datos de control (*Ground Truth*) etiquetado manualmente.

Este proceso genera métricas de evaluación como:

- Precisión (*Precision*).
- Exhaustividad (*Recall*).
- Comparación entre predicciones y etiquetas reales.
- Identificación de falsos positivos y falsos negativos.

Ejemplo de ejecución:

```bash
python -m auditor.validate
```

---

## 📄 Licencia

La licencia del proyecto debe definirse de acuerdo con las políticas de distribución y uso establecidas por la organización.