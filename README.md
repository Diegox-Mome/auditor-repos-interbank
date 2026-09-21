🛡️ Repo Test Auditor (Enterprise Edition)

Repo Test Auditor es una herramienta automatizada de análisis estático diseñada para auditar repositorios de GitHub a gran escala. Su objetivo es detectar la existencia, madurez y validez de pruebas unitarias en múltiples lenguajes de programación, mitigando de forma inteligente los falsos positivos generados por herramientas de scaffolding (plantillas autogeneradas).

Este motor fue diseñado bajo principios de gobierno de repositorios y automatización, asegurando la escalabilidad necesaria para auditar ecosistemas corporativos (ej. +2,000 repositorios) sin comprometer el rendimiento de la red.

🚀 Características y Decisiones de Arquitectura

El diseño de esta herramienta prioriza la eficiencia y la extensibilidad mediante cuatro pilares fundamentales:

Análisis sin Clonación (Blobless/Tree API): En entornos empresariales, ejecutar git clone sobre miles de repositorios satura el almacenamiento y el ancho de banda. Este motor se conecta a la API de GitHub (o realiza clones blobless) para extraer únicamente el árbol de archivos (Git Trees) y el contenido estrictamente necesario para el análisis.

Sistema Anti Falsos Positivos: La simple existencia de un archivo *Test.java o *.spec.ts no garantiza la calidad. El motor analiza semánticamente una muestra de los archivos de prueba para descontar casos triviales o de plantilla (ej. contextLoads() de Spring Boot o should create de Angular CLI). Si un repositorio solo contiene pruebas autogeneradas, se clasifica como carente de pruebas reales.

Diseño Data-Driven (Agnóstico al Lenguaje): El núcleo del motor en Python está completamente desacoplado de las reglas de negocio. Las reglas de detección para cada lenguaje (manifiestos, expresiones regulares, comandos de CI) residen en un archivo declarativo (rules.yaml). Esto permite incorporar nuevas tecnologías sin modificar el código fuente.

Escalabilidad y Manejo de Límites de Red:

Multithreading: Implementación de procesamiento paralelo (--workers) para maximizar el throughput.

Control de Rate Limit: El cliente HTTP intercepta respuestas 403/429, calculando el X-RateLimit-Reset para pausar y reanudar la ejecución de forma automática, evitando bloqueos por exceder la cuota de la API.

Caché con ETag/SHA: Implementación de caché transaccional para omitir descargas redundantes en ejecuciones recurrentes.

🛠️ Instalación y Configuración
1. Clonar el repositorio
git clone https://github.com/Diegox-Mome/auditor-repos-interbank.git
cd auditor-repos-interbank

2. Instalar dependencias

Se requiere Python 3.10 o superior.

pip install -r requirements.txt

3. Configurar Autenticación

Para analizar repositorios privados o realizar auditorías masivas sin enfrentar límites restrictivos de cuota (Rate Limits), se requiere configurar un Personal Access Token (PAT) de GitHub con permisos de lectura (repo).

Configuración como Variable de Entorno (Recomendado por seguridad):

En Windows (PowerShell):

$env:GITHUB_TOKEN = "ghp_tu_token_aqui"


En Linux/Mac:

export GITHUB_TOKEN="ghp_tu_token_aqui"


Nota: El token viaja exclusivamente como cabecera HTTP de autorización y nunca se expone en la línea de comandos ni en el código.

💻 Uso de la Herramienta

La interfaz de línea de comandos (CLI) permite ejecutar auditorías en diferentes modalidades.

Modo Demo (Lote de muestra)

Analiza una lista de repositorios específicos desde un archivo de texto utilizando el backend de la API REST:

python -m auditor --repos-file samples/demo_repos.txt --backend api --workers 6

Modo Corporativo (Auditoría Organizacional)

Ejecuta el escaneo de todos los repositorios pertenecientes a una organización de GitHub:

python -m auditor --org nombre_organizacion --workers 8

Compatibilidad con GitHub Enterprise Server

Para instancias On-Premise:

python -m auditor --org nombre_organizacion --api-url https://ghe.miempresa.com/api/v3

🧠 Flujo de Evaluación de Confianza

Para cada repositorio auditado, el motor evalúa tres capas independientes de evidencia para asignar un Nivel de Confianza (Alta, Media o Baja) al veredicto final:

Evidencia Declarada: ¿El framework de pruebas (ej. junit, jest) está configurado en las dependencias del proyecto (pom.xml, package.json)?

Evidencia Presente: ¿Existen directorios o archivos que cumplen con los patrones de nomenclatura de pruebas unitarias y contienen aserciones o casos reales?

Evidencia Ejecutada: ¿El pipeline de Integración Continua (CI/CD) contiene comandos que automatizan la ejecución de dichas pruebas?

Si todas las capas convergen, la confianza es Alta. Las inconsistencias (ej. dependencias declaradas pero sin archivos de prueba) reducen el nivel de confianza, alertando sobre repositorios que requieren revisión manual o refactorización.

🧩 Extensibilidad: Incorporación de Nuevas Tecnologías

La arquitectura permite soportar nuevos lenguajes con un esfuerzo mínimo. Para agregar, por ejemplo, Ruby, solo se requiere incorporar el siguiente bloque de metadatos en el archivo rules.yaml:

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


La incorporación de una nueva tecnología no requiere modificar el núcleo del motor, siempre que pueda expresarse mediante las reglas declarativas soportadas por rules.yaml.

📊 Salida de Datos y Reportería

Al concluir el análisis, el motor estructura los resultados en el directorio output/:

results.csv: Tabla consolidada con el listado de repositorios, la tecnología principal, el veredicto booleano de calidad y el nivel de confianza.

detail.csv: Desglose pormenorizado de las tres capas de evidencia para auditorías técnicas profundas.

summary.md: Documento de texto plano con los insights ejecutivos y métricas de cobertura organizacional.

results.jsonl: Checkpoint transaccional que permite reanudar el análisis mediante el flag --resume en caso de interrupción del proceso.

🧪 Testing y Validación Continua

La herramienta incluye una suite de pruebas automatizada (Test-Driven) para garantizar su propia integridad, abarcando 48 escenarios y validaciones de la API mockeada.

Para ejecutar la suite:

python -m pytest -q

Validación contra Ground Truth

Adicionalmente, se provee el módulo auditor.validate para cruzar los resultados predictivos del motor contra un Dataset de Control (Ground Truth) etiquetado manualmente.

Este proceso permite generar métricas matemáticas de Precisión (Precision) y Recall, facilitando la evaluación objetiva del desempeño del motor de detección.