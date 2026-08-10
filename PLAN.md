# Plan de trabajo — Sistema de telemetría LoRa

## Cómo usar este documento

Cada fase es **independiente y ejecutable en frío**: contiene todas las constantes, fórmulas y
criterios que necesita, sin depender de conversaciones previas. Esto es deliberado, porque al
terminar cada fase se ejecuta `/clear` y se pierde el contexto acumulado.

**Regla de trabajo:** ejecutar una sola fase por sesión. Al terminar, verificar con el criterio de la
fase y ejecutar `/clear` antes de empezar la siguiente.

---

## Contexto del proyecto (leer antes de cualquier fase)

Sistema de telemetría donde varias **Raspberry Pi Zero 2W** envían datos continuamente a un **PC
maestro Windows** mediante módulos LoRa **Waveshare USB-TO-LoRa-HF** (chip SX1262, interfaz USB-serie
con firmware DTU y comandos AT). El maestro guarda todo en CSV. Los datos alimentan un informe de
competencia, así que **el registro no puede tener huecos**: si el enlace se cae, los datos se
almacenan en el nodo y se reenvían íntegros al recuperarse.

**Hoy:** 3 módulos (2 nodos + 1 maestro). **Objetivo:** ~30 nodos contra 1 solo PC.

### Parámetros del sistema

| Parámetro | Valor | Origen |
|---|---|---|
| Frecuencia | Canal 70 = **920 MHz** | Banda libre Colombia 915–928 MHz |
| Factor de dispersión | **SF7** | Sobra margen de enlace a 600 m |
| Ancho de banda | **500 kHz** | Máxima capacidad; encaja con norma de modulación digital |
| Tasa de codificación | 4/5 | Mínima sobrecarga |
| Potencia | **10 dBm** | Quedan ~45 dB de margen; menos interferencia |
| Muestreo | 1 Hz por nodo | Requisito |
| Muestras por trama | **10** | Baja ocupación del canal de 54 % a 23 % |
| Superframe | **10 s** | 30 nodos → 41,5 % de ocupación |
| Slot por nodo | **136,9 ms** | 76,9 ms de trama + 60 ms de guarda |
| Límite del módulo | 240 B por paquete | Firmware DTU |

### Restricción legal (crítica)

**868 MHz no es legal en Colombia.** Cae en 851–915 MHz, que la ANE lista como banda restringida
(espectro celular licenciado). La banda libre sub-GHz es **915–928 MHz**.

**Frecuencia = 850 + canal (MHz).** Canales válidos: **65 a 78**. El canal 18 (valor de fábrica) da
868 MHz y **no debe usarse**. El código valida esto y se niega a arrancar fuera de rango salvo con el
flag explícito `allow_out_of_band`.

Las antenas de 868 MHz sirven a 920 MHz con desadaptación leve (~5 %); irrelevante con ~45 dB de
margen, pero conviene comprar antenas de 915 MHz a futuro.

### Comandos AT del módulo

Puerto serie **115200 8N1**. `+++\r\n` entra a modo comando, `AT+EXIT\r\n` sale — **el escape
necesita el `\r\n`**; enviar `+++` a secas no lo activa y el módulo se queda mudo sin dar ningún
error (verificado en hardware real en la Fase 1, ver notas críticas de esa fase).

```
AT+SF=7      factor de dispersión (7-12)     AT+ADDR=n    dirección propia (0-65535)
AT+BW=2      0=125k, 1=250k, 2=500k          AT+TXCH=70   canal TX -> 850+70 = 920 MHz
AT+CR=1      tasa de codificación 4/5        AT+RXCH=70   canal RX
AT+PWR=10    potencia 10-22 dBm              AT+NETID=n   identificador de red
AT+MODE=2    1=flujo, 2=paquete, 3=repetidor AT+LBT=0|1   escuchar antes de transmitir
AT+RSSI=1    añade byte de RSSI al final de cada paquete recibido
AT+VER       versión de firmware
```

**Dos trampas del hardware, con consecuencias directas en el código:**

1. `AT+RSSI=1` **añade un byte al final de cada paquete recibido**. El formato de trama debe ser
   autodelimitado para que ese byte no corrompa el mensaje.
2. `AT+LBT=1` **retrasa la transmisión hasta 2 segundos**. Eso destrozaría los slots TDMA:
   **LBT activado en modo ALOHA, desactivado en modo TDMA.**

**Modo paquete (`AT+MODE=2`):** los 3 primeros bytes escritos al puerto son
`[dir_alta][dir_baja][canal]` del destino, y el módulo los consume (no viajan como datos).

### Garantía de no pérdida de datos (tres reglas)

1. **Persistir antes de transmitir.** Cada muestra va a SQLite (modo WAL) con número de secuencia
   monótono *antes* de cualquier intento de envío.
2. **Confirmación acumulativa.** El maestro publica por nodo el `último_seq_contiguo` recibido, igual
   que TCP. Un solo número confirma todo lo anterior, así que perder un ACK no cuesta nada.
3. **Purgar solo lo confirmado.** Sin confirmación, el dato permanece en disco indefinidamente.

**Drenado intercalado:** al volver el enlace, el nodo alterna datos en vivo y atrasados (1:1 por
defecto), para no atascarse reenviando historia mientras pierde el presente.

### Estructura de archivos objetivo

```
lorasync/
  common/    config.py  frame.py  radio.py  schedule.py  airtime.py
  node/      store.py  sampler.py  main.py
  master/    store.py  csvsink.py  main.py
  tools/     at_console.py  linkcheck.py  to_wide.py
  tests/
  docs/      fundamentos-lora.tex  secciones/  figuras/  referencias.bib  Makefile
  config.example.toml   README.md   PLAN.md
```

**Dependencias:** solo `pyserial`. Todo lo demás es biblioteca estándar (`sqlite3`, `csv`, `struct`,
`tomllib`). Importa en la Zero 2W, donde compilar dependencias pesadas es lento y frágil.

---

# FASE 0 — Entorno y fundamentos del documento ✅ COMPLETADA (2026-08-03)

## Estado

**Hecha.** Se instaló la cadena de compilación LaTeX, se creó toda la estructura de `docs/` y se
escribieron y verificaron (compilación + inspección visual de cada figura TikZ + extracción de
texto para acentos/ñ) las Partes I, II y III. El PDF compila limpio: 30 páginas, sin errores, sin
referencias sin resolver, sin warnings de BibTeX. Queda un único `Overfull \hbox` de 16,6 pt
(~0,6 cm) en la leyenda de la Figura 10.1; se verificó visualmente que no produce ningún
desbordamiento perceptible y se dejó así.

### Notas críticas para las próximas fases

**1. PATH del sistema roto (afecta cualquier fase que use herramientas de línea de comandos).**
La variable de entorno `Path` a nivel de **Machine** contiene la entrada literal
`C:\Users\Javi\anaconda3\python.exe` (un archivo, no un directorio). Esto es un resto de una
sesión anterior que fijó Anaconda como Python por defecto. Rompe cualquier herramienta que itere
el PATH esperando solo directorios (le rompió el arranque a MiKTeX en esta fase). **No se corrigió
a nivel de sistema** porque es un cambio de entorno compartido fuera del alcance de esta fase; se
evitó localmente filtrando esa entrada en cada sesión de PowerShell antes de invocar `pdflatex`/
`latexmk`. Si en la Fase 1 (Python) aparecen errores raros de PATH, esta es la causa más probable;
la corrección es reemplazar esa entrada por `C:\Users\Javi\anaconda3` en las variables de entorno
del sistema (Panel de control → Variables de entorno), o pedirle al usuario que lo haga.

**2. MiKTeX y Strawberry Perl no quedaron en el PATH permanente.** Se instalaron con `winget`
(`MiKTeX.MiKTeX`, `StrawberryPerl.StrawberryPerl`) pero el instalador no actualizó el PATH de la
sesión de PowerShell (haría falta una terminal nueva para heredarlo, y aun así puede chocar con el
problema del punto 1). Para compilar el documento en la Fase 5 (o antes, si se necesita), anteponer
manualmente al `$env:Path`:
```powershell
C:\Strawberry\perl\bin
C:\Users\Javi\AppData\Local\Programs\MiKTeX\miktex\bin\x64
```
`latexmk` requiere Perl (`perl.exe`) para funcionar; sin Strawberry Perl en el PATH falla con
"MiKTeX could not find the script engine 'perl'".

**3. Comando de compilación verificado** (desde `docs/`):
```powershell
latexmk -pdf -interaction=nonstopmode -halt-on-error fundamentos-lora.tex
```
`latexmk -C fundamentos-lora.tex` limpia todos los artefactos. El mensaje de stderr
`pdflatex: major issue: So far, you have not checked for MiKTeX updates.` es ruido inofensivo, no
un error (MiKTeX sigue devolviendo código de salida 0 / el PDF se genera igual).

**4. Gotchas de LaTeX en español que ya se resolvieron y hay que recordar para las Partes IV-IX
(Fase 5):**
- `babel` con la opción `spanish` activa `<` y `>` como atajos para comillas («»). Cualquier
  `<->`, `<=`, etc. de TikZ los rompe. Solución ya aplicada en el preámbulo:
  `\usepackage[spanish,es-noindentfirst,es-noshorthands]{babel}`.
- El símbolo `\%` inmediatamente después de `\,` **dentro de modo matemático** (`\[ \]` o `$...$`)
  falla con "Incompatible glue units" por cómo `spanish` redefine `%`. Usar `\SI{6}{\percent}` (ya
  cargado `siunitx`) en vez de `6\,\%` cuando esté en modo matemático; en modo texto normal
  `6\,\%` sí funciona sin problema.
- Un `\node` de TikZ con `\\` para salto de línea falla con "Not allowed in LR mode" si no tiene
  `align=center` (o `text width`) en sus opciones. Patrón usado en todas las figuras de este
  documento: `every node/.style={align=center}` en las opciones del `tikzpicture`, o
  `align=center`/`text width=Ncm` por nodo cuando el texto es largo y hay que evitar que se
  superponga con otros elementos (pasó en la Figura 10.1 y en la Figura 6.1; ambas se corrigieron
  acotando el ancho del texto de cada nodo).
- Los tipos de entrada `@online` de BibLaTeX **no existen** en BibTeX clásico con
  `\bibliographystyle{plain}` (da warning "entry type isn't style-file defined" y no formatea
  bien esos campos). `referencias.bib` usaba `@online` para las fuentes web y ya se corrigió a
  `@misc` (`waveshare_usb_lora_hf`, `fcc_15247`, `ttn_au915`). Si se agregan más fuentes web en la
  Fase 5, usar `@misc`, no `@online`, salvo que se migre a BibLaTeX+biber (no es el caso aquí:
  `latexmk` ya resuelve bibtex clásico automáticamente al detectar `\bibliography`).

**5. Estructura creada:** `docs/fundamentos-lora.tex`, `docs/secciones/{01-radio,02-lora,
03-regulacion}.tex`, `docs/referencias.bib`, `docs/Makefile` (target `all`/`clean`/`cleanall` via
`latexmk`), `.gitignore` (artefactos LaTeX + Python + datos locales). Las Partes IV-IX se agregan
en la Fase 5; el `\input` de esos archivos ya está pre-escrito y comentado en
`fundamentos-lora.tex`, solo hay que descomentarlo al crear cada `.tex`.

---

## Objetivo (texto original de la fase, para referencia)
Dejar funcionando la cadena de compilación LaTeX y escribir las tres primeras partes del documento
didáctico (las que no dependen de código). Se hace primero para no descubrir fallos de compilación
cuando ya haya 40 páginas escritas.

## Archivos a crear
- `docs/fundamentos-lora.tex` — documento principal, con portada, índice y `\input` de secciones
- `docs/secciones/01-radio.tex` — Parte I: fundamentos de radio
- `docs/secciones/02-lora.tex` — Parte II: cómo funciona LoRa
- `docs/secciones/03-regulacion.tex` — Parte III: el espectro como recurso legal
- `docs/referencias.bib` — datasheet SX1262, resoluciones ANE, wiki Waveshare
- `docs/Makefile` — compilación con `latexmk`
- `.gitignore` — artefactos de LaTeX (`*.aux`, `*.log`, `*.out`, `*.toc`, `*.fdb_latexmk`)

## Instalación
```powershell
winget install MiKTeX.MiKTeX
```
Tras instalar, abrir una terminal nueva (el PATH cambia). MiKTeX descarga paquetes bajo demanda: en la
primera compilación conviene permitir la instalación automática.

## Contenido a redactar
Cada concepto sigue tres pasos: **qué es** (desde cero) → **por qué importa aquí** → **dónde aparece
en el código**. Tono didáctico, con analogías y ejemplos numéricos resueltos.

- **Parte I:** qué es una onda de radio y qué significa 920 MHz. Decibelios y dBm: por qué la escala
  es logarítmica. Potencia, ganancia de antena y PIRE. Presupuesto de enlace con el cálculo de los
  600 m resuelto íntegro. Adaptación de antena y por qué una de 868 MHz sirve a 920 MHz.
- **Parte II:** qué es modular una señal. Espectro ensanchado por chirp con analogía. Factor de
  dispersión: el compromiso alcance/velocidad. Ancho de banda y corrección de errores. **La fórmula
  del tiempo de aire desglosada término a término.** Por qué un SF alto no siempre es mejor.
- **Parte III:** por qué se regula el espectro y qué son las bandas ISM. Qué es la ANE. Por qué
  915–928 MHz sí y 868 MHz no. Ciclo de trabajo y convivencia.

Figuras con **TikZ** (dibujo por código dentro del propio LaTeX), para que el documento sea
autocontenido y editable sin herramientas externas.

### Cálculos que deben aparecer resueltos

```
Pérdida de espacio libre a 600 m y 920 MHz:
  FSPL = 32,45 + 20·log10(920 MHz) + 20·log10(0,6 km) = 87,3 dB
  RX = 10 dBm + 2 dBi + 2 dBi − 87,3 dB = −73,3 dBm
  Sensibilidad SF7/500 kHz ≈ −118 dBm  →  margen ≈ 45 dB

Tiempo de aire (SF7, BW 500 kHz, CR 4/5, preámbulo 8, CRC activo):
  Tsym = 2^SF / BW = 2^7 / 500000 = 0,256 ms
  Tpreámbulo = (8 + 4,25) · Tsym
  n_payload = 8 + max(ceil((8·PL − 4·SF + 28 + 16) / (4·SF)) · (CR+4), 0)
  Ttrama = Tpreámbulo + n_payload · Tsym
  Con PL = 194 B  →  76,9 ms
```

## Criterio de verificación
1. `pdflatex --version` responde en una terminal nueva.
2. `cd docs && latexmk -pdf fundamentos-lora.tex` termina sin errores.
3. Se genera `fundamentos-lora.pdf` con portada, índice y las tres partes.
4. El PDF abre correctamente y los diagramas TikZ se ven bien.
5. Los acentos y la 'ñ' se muestran correctos (comprobar codificación UTF-8).

-> Ejecutar /clear al terminar

---

# FASE 1 — Capa de radio ✅ COMPLETADA (2026-08-08)

## Estado

**Hecha.** Los 6 archivos están escritos, `pytest tests/` pasa 9/9, y se verificaron los 6 criterios
con hardware real: 2 dongles (PC Windows, addr=10, `COM11`; Raspberry Pi Zero 2W por SSH, addr=20,
`/dev/ttyACM1`), ambos en canal 70/SF7/BW500k/10dBm, con `apply_config()` confirmado por relectura y
un `send()`/`receive()` de `b"hola"` con RSSI plausible. Faltó probar un tercer módulo (el plan
original habla de 3: 2 nodos + 1 maestro) por no tener aún el segundo nodo físico; se validó con los
2 disponibles y no hay razón para esperar que un tercero se comporte distinto.

### Notas críticas para las próximas fases

**1. El escape `+++` necesita `\r\n` — sin eso, silencio total sin ningún error.**
`common/radio.py` y `tools/at_console.py` originalmente enviaban `b"+++"` a secas, copiando la
convención genérica de módems. La wiki oficial de Waveshare
(https://www.waveshare.com/wiki/USB-TO-LoRa-xF) documenta `+++\r\n` / `AT+EXIT\r\n`. El síntoma sin
el `\r\n` es indistinguible de un módulo muerto: cero bytes de respuesta a cualquier comando, con
enumeración USB limpia y LEDs normales. Ya corregido en ambos archivos. Si se porta este driver a
otro firmware/módulo, verificar el escape exacto antes de asumir `+++` puro.

**2. El módulo hace eco del comando antes de responder — no usar `readline()`.**
La respuesta real a `AT+VER` es `b'AT+VER\nVer1.2\n\r\nOK\r\n'`: la primera línea es el eco del
comando enviado, no la respuesta. `_send_at()` en `radio.py` originalmente usaba
`ser.readline()`, que se quedaba con esa primera línea (o con nada, según el timing). Se corrigió a
un patrón esperar-y-leer-todo, que captura eco + valor + `OK` completos. Los chequeos de
`_verify_config()` usan `in` (substring) precisamente porque la respuesta cruda incluye el eco.

La primera versión de ese patrón dormía `time.sleep(AT_TIMEOUT_S)` (1 s) antes de leer, con lo que
cada comando costaba 1 s fijo y una `apply_config()` completa tardaba **16,4 s medidos**. Como el
módulo está en modo AT durante todo ese tiempo, la radio queda sorda: con `hello_test` de un solo
disparo en ambos extremos, basta un desfase de unos segundos entre los dos procesos para que el
paquete caiga en la ventana sorda del receptor y la prueba falle sin dejar ningún rastro. Se
reemplazó por detección de silencio (`AT_POLL_S = 0.02`, `AT_QUIET_S = 0.2`): se sondea
`in_waiting` y se da la respuesta por terminada tras 0,2 s sin bytes nuevos, con `AT_TIMEOUT_S`
como tope duro. Misma respuesta capturada, `apply_config()` baja a **4,0 s medidos**. La
configuración además persiste en la flash del módulo, así que para pruebas repetidas conviene
`hello_test --skip-config`, que evita el modo AT por completo.

**3. `AT+MODE=2` debe aplicarse *después* de verificar SF/BW/canales, no antes.**
El orden original de `apply_config()` aplicaba `AT+MODE=2` (pasar a modo paquete) y luego intentaba
releer `AT+SF?`/`AT+TXCH?`/etc. para verificar. Cambiar a modo paquete a mitad de una sesión AT
parece alterar cómo el módulo interpreta los bytes siguientes, y **todas** las consultas de
verificación posteriores volvían vacías (no solo una). Se corrigió moviendo `_verify_config()` antes
de `AT+MODE=2`. Si se agregan más parámetros a `apply_config()`, mantener cualquier cambio de modo
operativo (`AT+MODE`) como el último comando de la secuencia.

**4. Límite real de hardware: reentrar a modo AT tras un `AT+EXIT` previo puede no funcionar.**
Verificado en el dongle del PC (chip CH343, Windows): tras `+++` → comandos → `AT+EXIT`, un segundo
`+++` en el mismo proceso no obtiene respuesta — ni con más espera (probado hasta 1.5 s extra), ni
reseteando el buffer, ni cerrando y reabriendo el puerto COM desde cero. El dongle de la Raspberry Pi
(chip WCH distinto, Linux) sí lo permitió sin problema. Como es una diferencia real entre firmwares/
drivers y no un bug del código, el driver no reintenta automáticamente
(`common/radio.py::enter_at_mode`, comentado con `# ponytail:`). **Regla práctica: no encadenar
`version()` y `apply_config()` (u otras dos operaciones en modo AT) sobre el mismo objeto `Radio` en
un solo proceso** — cada una debe abrir su propio `Radio`/puerto si se necesitan ambas.

**5. Fórmula de RSSI corregida: `-byte/2`, no `byte - 256`.**
La implementación original asumía una convención de byte con signo (`rssi_byte - 256`), que para
paquetes reales dio valores físicamente imposibles como −205 dBm. La convención real es la del
registro `RssiInst` del SX1262: **RSSI (dBm) = −byte / 2** (pasos de 0,5 dB, byte sin signo). Con un
byte crudo de 51, la fórmula vieja daba −205 dBm; la correcta da −25,5 dBm, coherente con dos dongles
uno junto al otro en una prueba de banco. Corregido en `common/radio.py::receive()`; el tipo de
retorno pasó de `int | None` a `float | None`.

**6. El botón físico `KEY` del dongle NO es para entrar a modo AT.**
Es para modo de actualización de firmware (mantener 2 s dentro de los primeros 3 s tras encender) y
para restaurar valores de fábrica (mantener 2 s después de los primeros 3 s tras encender). Modo AT
se entra siempre por software (`+++\r\n`) sin tocar el botón.

**7. `find_dongle_port()` en `common/radio.py` solo detecta `/dev/ttyUSB*` en Linux — no
`/dev/ttyACM*`.** El dongle usado (WCH/QinHeng, vendor `1a86`) enumera como `ttyACM*` (driver
`cdc_acm` estándar), no `ttyUSB*` (`ch341`). No se corrigió porque en la Fase 1 el puerto se pasó
explícito vía `config.toml`, pero **antes de la Fase 5 (despliegue desatendido) hay que arreglar
esta detección automática** o todos los nodos necesitarán puerto fijo a mano.

**8. Raspberry Pi Zero 2W: el dongle USB necesita alimentación robusta.**
Con la Pi alimentada desde el puerto USB del PC (vía el mismo cable de datos que se usaba para SSH/
alimentación), el dongle entraba en un bucle de enumeración fallida (`error -71`, `disabled by hub
(EMI?)`) — típico de subalimentación en el único puerto OTG de la Zero 2W. Se resolvió alimentando la
Pi desde un cargador de pared dedicado. Tener esto en cuenta al desplegar los ~30 nodos: cada Pi
necesita su propia fuente, no alimentación compartida vía el cable de datos.

**9. `config.toml` es local por máquina y no se versiona junto con `config.example.toml`.**
Cada dongle necesita su propio `port` y `addr`. Se usó `config.example.toml` como plantilla y se creó
un `config.toml` real por máquina (PC: `port="COM11"`, `addr=10`; Pi: `port="/dev/ttyACM1"`,
`addr=20`) — no editar `config.example.toml` directamente con los valores de una máquina concreta.

## Objetivo
Poder configurar y usar los dongles desde Python: abrir el puerto, aplicar la configuración validada
y enviar/recibir bytes. Sin protocolo todavía; solo la capa que habla con el hardware.

## Archivos a crear
- `common/config.py` — dataclass de configuración + carga desde TOML.
  **Debe validar que el canal esté en 65–78** y rechazar el arranque en caso contrario, salvo que
  `allow_out_of_band = true`. Mensaje de error explícito citando la banda legal colombiana.
- `common/radio.py` — driver: abrir puerto, entrar/salir de modo AT (`+++` / `AT+EXIT`), aplicar
  configuración, transmitir en modo paquete (anteponiendo los 3 bytes de destino), recibir y
  **separar el byte de RSSI del final**.
- `common/airtime.py` — cálculo de tiempo de aire, duración de slot y capacidad del canal.
- `tools/at_console.py` — consola AT interactiva para configurar y diagnosticar dongles a mano.
- `config.example.toml` — configuración de ejemplo comentada.
- `tests/test_config.py`, `tests/test_airtime.py`

## Detalles de implementación
- Detección automática de puerto: en Windows `COM*`, en Linux `/dev/ttyUSB*`. Filtrar por el
  identificador USB del conversor serie si es posible.
- Entrar a modo AT requiere una pausa de guarda antes y después del `+++` (empezar con 200 ms).
- Tras aplicar configuración, **releer los valores** (`AT+TXCH?` etc.) y confirmar que coinciden;
  no dar por hecho que el módulo aceptó el comando.
- `airtime.py` debe reproducir exactamente los valores de la tabla de parámetros: 76,9 ms para una
  trama de 194 B en SF7/500 kHz.

## Criterio de verificación
1. `python -m tools.at_console` detecta el dongle y `AT+VER` devuelve la versión de firmware.
2. Configurar los 3 módulos y confirmar releyendo que están en canal 70, SF7, BW 500 kHz, 10 dBm.
3. Un canal fuera de 65–78 en la configuración **impide el arranque** con mensaje claro.
4. Prueba de humo entre dos dongles: enviar `b"hola"` de uno a otro y recibirlo íntegro.
5. Con `AT+RSSI=1`, el receptor reporta un valor de RSSI plausible (entre −30 y −120 dBm)
   **y el byte de RSSI no aparece dentro de los datos**.
6. `pytest tests/test_airtime.py` pasa: 76,9 ms para PL = 194 B.

-> Ejecutar /clear al terminar

---

# FASE 2 — Trama y almacenamiento ✅ COMPLETADA (2026-08-08)

## Estado

**Hecha.** Los 3 archivos están escritos (`common/frame.py`, `node/store.py`, `master/store.py`),
`pytest tests/` pasa 28/28 (9 de fases previas + 19 nuevos), y los 9 criterios de verificación están
cubiertos por tests concretos. Sin hardware, todo simulado como pedía la fase.

### Notas críticas para las próximas fases

**1. Anchos de campo de la cabecera (el plan no los fijaba) — importan para Fase 3/4/5.**
`ver`=1B, `tipo`=1B, `id_nodo`=2B (coincide con `AT+ADDR` 0-65535), `seq_inicial`=4B (uint32,
136 años a 1 Hz antes de desbordar — con 2B se desbordaría a las ~18h, insuficiente para una
competencia de varios días), `n_muestras`=1B. Cabecera = 9B. Todo big-endian (`struct` con `>`).

**2. Con estos anchos, el tamaño real de trama de 10 muestras de 18B difiere ~1B del PL=194B
asumido en Fase 0/1 para `airtime.py`.** Cabecera(9) + payload(180) + CRC16(2) = 191B pre-COBS;
COBS añade como mucho 1 byte de overhead cada 254B + el delimitador 0x00 final → ~193B, no 194B.
Diferencia mínima (no cambia el tiempo de aire de forma perceptible), pero al construir
`node/main.py` en la Fase 3, recalcular `airtime.frame_time_s()` con el tamaño real que produzca
`encode_frame()`, no asumir 194B a ciegas.

**3. CRC16 = CCITT-FALSE (poly 0x1021, init 0xFFFF), elección arbitraria sin significado externo.**
No hay protocolo de terceros que exija una variante concreta — nodo y maestro corren el mismo
código de `frame.py`, así que la elección solo tiene que ser consistente consigo misma. Si en la
Fase 5 se documenta COBS/CRC16 en la Parte VI, usar esta variante y este vector de prueba conocido:
`crc16_ccitt_false(b"123456789") == 0x29B1`.

**4. `seq INTEGER PRIMARY KEY` en `node/store.py` se implementó como `AUTOINCREMENT`, desviación
deliberada del DDL literal del plan.** Sin `AUTOINCREMENT`, SQLite reasigna `ROWID` desde 1 cuando
la tabla queda completamente vacía (lo que pasa con normalidad: cada muestra se confirma y se purga
si el enlace va bien) — eso reutilizaría números de secuencia, violando la garantía "seq nunca se
reutiliza" que el propio comentario del schema exige. Verificado con
`test_seq_never_reused_even_after_full_purge`.

**5. Sin repositorio git todavía.** El usuario decidió no inicializar git para este proyecto por
ahora (aunque `.gitignore` ya existe desde la Fase 0). Si se retoma esta decisión en una fase
posterior, no hay historial que reconstruir: los archivos en disco son el único estado.

**6. `node/__init__.py` y `master/__init__.py` se crearon vacíos** (antes no existían, por eso
`node/store.py` y `master/store.py` no eran importables como paquete). Mismo patrón que
`common/__init__.py` y `tools/__init__.py`.

## Objetivo
Construir el formato de trama a prueba de corrupción y el almacén del nodo que sobrevive a cortes de
luz. **Sin hardware:** todo se prueba con puertos serie simulados.

## Archivos a crear
- `common/frame.py` — codificación/decodificación COBS, CRC16, cabecera.
- `node/store.py` — SQLite en modo WAL: añadir muestra, leer pendientes, marcar confirmado, purgar.
- `master/store.py` — SQLite: deduplicación por `(nodo, seq)` y estado por nodo.
- `tests/test_frame.py`, `tests/test_node_store.py`, `tests/test_master_store.py`

## Formato de trama
```
[dir_alta][dir_baja][canal]   <- consumidos por el módulo (modo paquete)
COBS( ver | tipo | id_nodo | seq_inicial | n_muestras | payload | CRC16 ) 0x00
                                                           ^ el byte de RSSI cae aquí y se ignora
```

**Por qué COBS:** elimina todos los bytes 0x00 del contenido, con lo que el 0x00 final es un
delimitador inequívoco. Sin esto, un byte nulo dentro de un `float` partiría la trama por la mitad.
El CRC16 se valida siempre, además del CRC que ya aplica el módulo.

**Tipos de trama:** `DATA`, `ACK`, `HELLO` (alta de nodo y declaración de esquema), `TIME` (hora del
maestro, para nodos sin fix GPS).

## Esquema de base de datos
```sql
-- nodo
CREATE TABLE muestras (
  seq INTEGER PRIMARY KEY,      -- monótono, nunca se reutiliza
  ts_utc REAL NOT NULL,
  payload BLOB NOT NULL,
  confirmado INTEGER DEFAULT 0
);
CREATE INDEX idx_pendientes ON muestras(confirmado, seq);
PRAGMA journal_mode=WAL;

-- maestro
CREATE TABLE recibidas (
  nodo_id INTEGER, seq INTEGER, ts_utc REAL, ts_nodo REAL,
  rssi INTEGER, payload BLOB,
  PRIMARY KEY (nodo_id, seq)    -- la deduplicación es automática
);
CREATE TABLE estado_nodo (
  nodo_id INTEGER PRIMARY KEY,
  ultimo_seq_contiguo INTEGER,  -- lo que se confirma en el ACK
  visto_por_ultima_vez REAL
);
```

## Criterio de verificación
1. **Ida y vuelta de trama** con payloads que contengan bytes 0x00 en todas las posiciones.
2. **Tolerancia al RSSI:** una trama con un byte extra al final se decodifica correctamente.
3. **Rechazo de corrupción:** alterar cualquier bit hace fallar el CRC; ninguna trama corrupta se
   acepta como válida.
4. Tramas truncadas y tramas concatenadas se manejan sin excepciones no controladas.
5. **Durabilidad:** matar el proceso a mitad de escritura (`os._exit`) y comprobar al reabrir que la
   base de datos está íntegra y no falta ninguna muestra confirmada.
6. Los pendientes salen siempre en orden de secuencia.
7. La purga **nunca** borra filas no confirmadas.
8. En el maestro, insertar el mismo `(nodo, seq)` dos veces no duplica la fila.
9. `ultimo_seq_contiguo` no avanza más allá de un hueco.

-> Ejecutar /clear al terminar

---

# FASE 3 — Enlace extremo a extremo (ALOHA) ⏸ CÓDIGO LISTO, FALTA HARDWARE

## Estado

**Código completo, sin hardware.** Los 7 archivos están escritos (`common/schedule.py`,
`node/sampler.py`, `node/main.py`, `master/csvsink.py`, `master/main.py`, `README.md`,
`tests/test_e2e_simulado.py`), más 2 adiciones no invasivas a `common/frame.py`
(`PROTOCOL_VERSION`, `split_stream` para separar tramas+RSSI en un flujo continuo) y
`common/radio.py` (`read_bytes`, lectura de tamaño variable para tramas COBS). `pytest tests/`
pasa 29/29, incluido el ciclo completo simulado (nodo y maestro en hilos separados sobre
puertos serie falsos, con arranque tardío del maestro imitando un corte): sin huecos ni
duplicados en el CSV resultante.

**Falta retomar con hardware real** (criterios 2-8 de más abajo, ninguno cubierto todavía):
arrancar maestro + 2 nodos, apagar el maestro 10 min con los nodos muestreando, reencenderlo y
verificar secuencias contiguas; repetir desconectando antena y cortando alimentación a mitad de
escritura; confirmar el drenado intercalado en vivo.

### Notas para retomar

- **Formato de trama del lote:** payload = `ts_base` (float64, 8B) + por muestra
  `ts_diff_ms(uint16, 2B) + 4 floats (16B)` = 18B/muestra. Ver `node/sampler.py` (`encode_batch`/
  `decode_batch`). No se tocó el header de `common/frame.py` de la Fase 2.
- **ACK reutiliza los campos existentes de `Frame`:** `tipo=ACK`, `id_nodo`=nodo destino,
  `seq_inicial`=último_seq_contiguo confirmado, `n_muestras=0`, `payload=b""`. No hizo falta
  agregar un tipo de trama nuevo.
- **`node/main.py` drena la cola completa al final de un `run_node(..., iterations=N)` acotado**
  (cierre ordenado o prueba), para no dejar un resto de <10 muestras varado. En despliegue real
  (`iterations=None`) el bucle no termina nunca, así que esto no aplica salvo que se agregue un
  cierre ordenado explícito más adelante.
- **`config.example.toml`** ganó secciones `[node]` (`master_addr`, `db_path`) y `[master]`
  (`db_path`, `csv_dir`); `lbt` se puso en `true` (obligatorio en ALOHA, ver PLAN.md arriba).
- Antes de la prueba con hardware: copiar `config.example.toml` a `config.toml` en cada máquina
  con `port`/`addr` reales (ver nota 9 de la Fase 1), y `master_addr` correcto en cada nodo.

-> NO ejecutar /clear todavía — retomar esta misma fase con hardware antes de pasar a Fase 4.

---

## Objetivo
**Sistema completo y funcional con los 3 módulos**, cumpliendo el requisito de no perder datos. Es el
prototipo entregable: al terminar esta fase ya tienes telemetría real guardándose en CSV.

## Archivos a crear
- `common/schedule.py` — interfaz de planificador + `AlohaScheduler` (retardo aleatorio, LBT activo).
- `node/sampler.py` — fuente de datos. **Stub reemplazable**: genera datos sintéticos hasta que se
  definan los sensores reales. Aquí es donde se conectan después.
- `node/main.py` — bucle del nodo: muestrear → persistir → agrupar 10 → transmitir → escuchar ACK →
  purgar confirmados.
- `master/main.py` — bucle del maestro: recibir → validar → deduplicar → escribir CSV → enviar ACK.
- `master/csvsink.py` — CSV largo, append-only, rotación diaria.
- `README.md` — instrucciones de puesta en marcha.
- `tests/test_e2e_simulado.py` — nodo y maestro conectados por puertos serie falsos.

## Formato CSV (largo, estructura inmutable)
```csv
ts_utc,ts_nodo,nodo_id,seq,variable,valor,rssi
2026-08-03T22:14:03.120Z,2026-08-03T22:14:03.008Z,10,84213,temp_c,24.71,-73
2026-08-03T22:14:03.120Z,2026-08-03T22:14:03.008Z,10,84213,volt,12.04,-73
```

La cabecera **nunca cambia**, aunque se agreguen sensores después. Se guardan las dos marcas de tiempo
(la del nodo y la de llegada al maestro) para poder detectar desfases de reloj a posteriori.

## Criterio de verificación

**Sin hardware:**
1. `pytest tests/test_e2e_simulado.py` — ciclo completo por puertos simulados.

**Con hardware — la prueba que de verdad importa:**
2. Arrancar maestro y 2 nodos. Confirmar que el CSV crece con datos de ambos.
3. **Apagar el maestro 10 minutos** mientras los nodos siguen muestreando.
4. Volver a encender el maestro.
5. **Criterio de éxito:** al terminar el drenado, el CSV contiene **todos los números de secuencia,
   sin huecos y sin duplicados**, durante todo el corte. Verificar con un script que compruebe que la
   secuencia por nodo es contigua.
6. Repetir desconectando la antena de un nodo (degradación en vez de caída limpia).
7. Repetir cortando la alimentación de un nodo a mitad de escritura (durabilidad de SQLite).
8. Comprobar que los datos en vivo siguen llegando **mientras** se drenan los atrasados (intercalado).

-> Ejecutar /clear al terminar

---

# FASE 4 — TDMA y escalado a 30 nodos

## Objetivo
Sustituir el acceso aleatorio por turnos coordinados, que rinden unas 3 veces más. Es lo que lleva el
sistema de 2 a 30 nodos.

## Archivos a modificar/crear
- `common/schedule.py` — añadir `TdmaScheduler`.
- `master/main.py` — ACK broadcast y asignación de slots.
- `node/main.py` — registro vía `HELLO`, sincronización horaria, degradación automática.
- `tests/test_tdma.py`

## Diseño del superframe
```
Superframe = 10 s
Slot de nodo = 76,9 ms (trama de 194 B) + 60 ms de guarda = 136,9 ms
ACK broadcast = 44,9 ms (confirma a los 30 nodos en UNA sola trama)

inicio_slot = floor(t / 10) · 10 + id_slot · 0,1369
```

La guarda de 60 ms cubre la jitter de planificación del sistema operativo y del USB en la Zero 2W,
que es bastante mayor que el error del GPS.

| Nodos | Ocupación | Slots libres |
|---|---|---|
| 2 | 3,2 % | 70 |
| 10 | 14,1 % | 62 |
| **30** | **41,5 %** | **42** |
| 50 | 68,9 % | 22 |

Capacidad máxima del canal: **~72 nodos a 1 Hz**.

**ACK broadcast:** el maestro confirma a los 30 nodos con una sola trama de 104 B en vez de 30 ACK
individuales. Es el mayor ahorro de tiempo de aire del diseño.

**Degradación automática:** si el nodo pierde la referencia horaria (sin GPS ni NTP), **cae solo a
ALOHA** en lugar de transmitir en slots equivocados y colisionar con los vecinos. Esto es obligatorio,
no opcional.

**LBT desactivado en TDMA** (su retardo de hasta 2 s rompería los slots).

## Criterio de verificación
1. Los límites de slot son correctos alrededor del cambio de superframe (probar en el borde exacto).
2. Dos nodos con identificadores distintos **nunca** solapan sus ventanas de transmisión.
3. Simulación con 30 nodos virtuales: ocupación medida ≈ 41,5 %, sin colisiones.
4. Un nodo sin referencia horaria pasa a ALOHA automáticamente y sigue entregando datos.
5. El ACK broadcast confirma correctamente a varios nodos con una sola trama.
6. Con hardware: los 2 nodos reales en modo TDMA mantienen entrega íntegra durante 1 hora seguida.
7. Medir el tiempo real de recuperación tras un corte y contrastarlo con lo previsto
   (1 nodo caído 1 h ≈ 1,4 min de drenado).

-> Ejecutar /clear al terminar

---

# FASE 5 — Herramientas, despliegue y cierre del documento

## Objetivo
Dejar el sistema operable de forma desatendida durante largos periodos y cerrar el documento
didáctico con las partes que dependen del código ya escrito.

## Archivos a crear
- `tools/linkcheck.py` — diagnóstico de enlace: RSSI, tasa de pérdida, tiempo de ida y vuelta.
- `tools/to_wide.py` — conversión de CSV largo a ancho para Excel/pandas.
- `deploy/lorasync-node.service` — servicio systemd: arranque automático y reinicio ante fallo.
- `docs/secciones/04-modulo.tex` — Parte IV: el módulo Waveshare por dentro.
- `docs/secciones/05-canal.tex` — Parte V: compartir un canal entre muchos.
- `docs/secciones/06-integridad.tex` — Parte VI: integridad de los datos.
- `docs/secciones/07-persistencia.tex` — Parte VII: persistencia ante cortes de luz.
- `docs/secciones/08-sistema.tex` — Parte VIII: el sistema completo.
- `docs/secciones/09-apendices.tex` — glosario, tablas de referencia, fórmulas, errores típicos.

## Contenido de las partes restantes
- **IV:** anatomía del módulo (SX1262 + conversor USB-serie + firmware DTU). Qué es un puerto serie y
  qué significa "115200 8N1". De dónde vienen los comandos AT. Modos flujo, paquete y repetidor.
- **V:** el problema de la colisión, ilustrado. ALOHA y por qué solo aprovecha ~18 % del canal. CSMA y
  LBT. TDMA y por qué exige reloj común. Comparación con los números reales del proyecto. Por qué el
  ACK broadcast ahorra tanto.
- **VI:** qué significa exactamente "no perder datos". El problema de delimitar mensajes. **COBS
  explicado byte a byte con un ejemplo completo.** De la paridad al CRC16. Números de secuencia y
  detección de huecos. ACK acumulativo: cómo lo resuelve TCP y por qué lo copiamos. Idempotencia.
- **VII:** por qué escribir en un archivo no basta (escrituras parciales y corrupción). Qué es SQLite.
  Transacciones y ACID en lenguaje llano. Qué es el modo WAL y de qué protege. Desgaste de microSD.
  Store-and-forward y drenado intercalado.
- **VIII:** la arquitectura repasada con todo el vocabulario ya aprendido. **Recorrido de una muestra
  de principio a fin**, del sensor a la fila del CSV. CSV ancho contra largo y qué es normalizar.
  Relojes: UTC, deriva, GPS y NTP, y por qué se guardan dos marcas de tiempo. Guía de diagnóstico.

## Criterio de verificación
1. `linkcheck.py` a 600 m reporta RSSI cercano a lo previsto (≈ −73 dBm a 10 dBm de potencia).
   Una desviación grande indica problema de antena o de configuración.
2. `to_wide.py` convierte un CSV largo real y el resultado abre bien en Excel.
3. El servicio systemd arranca solo tras reiniciar la Raspberry Pi.
4. Matar el proceso del nodo: systemd lo reinicia y **no se pierde ninguna muestra**.
5. `latexmk -pdf fundamentos-lora.tex` compila el documento completo sin errores.
6. El PDF final tiene índice navegable, todas las figuras y el glosario completo.
7. **Prueba de resistencia: 24 horas continuas** con 2 nodos. Al terminar, verificar secuencias
   contiguas sin huecos ni duplicados en todo el periodo.

-> Ejecutar /clear al terminar

---

## Supuestos declarados

- **Latencia de 10 s** por el agrupamiento. El muestreo sigue siendo a 1 Hz y no se pierde ningún
  dato; solo llegan en lotes. Configurable: `batch=1` da 1 s de latencia pero limita el sistema a
  ~12 nodos.
- **Muestra de 18 B** (marca de tiempo diferencial + ~4 variables float32). Con más variables por
  muestra hay que rehacer el dimensionamiento; `airtime.py` lo calcula.
- **Un dongle en el maestro.** El diseño admite varios en canales distintos, pero no se implementa
  hasta que haga falta. No es necesario para los 30 nodos.
- **`sampler.py` es un stub** hasta que se definan los sensores reales.
- **Nota regulatoria:** las condiciones de la banda 902–928 derivan del modelo FCC 15.247, que exige
  ancho de banda amplio o salto de frecuencia. Estos dongles son de canal fijo, así que no pueden
  cumplir la variante de salto. Los 500 kHz de ancho de banda son lo que mejor encaja con la condición
  de modulación digital. Para un despliegue formal conviene confirmarlo con la ANE.
