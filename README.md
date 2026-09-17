# lorasync

Telemetría por LoRa: varias Raspberry Pi Zero 2W ("nodos") envían muestras a 1 Hz a un PC
maestro Windows por dongles Waveshare USB-TO-LoRa-HF (SX1262). El maestro escribe todo a CSV.
Diseño y fundamentos completos en `docs/fundamentos-lora.pdf` (compilar con `docs/Makefile`).
Detalles de arquitectura, decisiones y trampas de hardware ya resueltas: ver `PLAN.md`.

## Configuración inicial (primera vez en cada equipo)

La configuración de radio (canal, SF, dirección...) queda guardada en la flash del dongle, pero
`master.main` y `node.main` la vuelven a aplicar y a verificar en cada arranque. Así que "cargarla"
consiste en dejar un `config.toml` correcto en cada máquina y comprobar el enlace una vez con
`tools.hello_test` antes de arrancar el sistema completo.

### 1. Instalar

PC maestro (Windows), con Python 3.11+:
```
git clone https://github.com/ItsJavi93/lorasync.git lorasync
cd lorasync
pip install pyserial
```

Cada nodo (Raspberry Pi OS Bookworm, que ya trae Python 3.11):
```
git clone https://github.com/ItsJavi93/lorasync.git lorasync
cd lorasync
sudo apt install python3-serial
```

Todo lo demás es biblioteca estándar (`tomllib`, `sqlite3`, `csv`, `struct`).

En la Pi Zero 2W, alimenta la placa con su propia fuente de pared: con el dongle y el cable de
datos compartiendo el único puerto OTG, el dongle no enumera (`error -71`, PLAN.md nota 8).

### 2. Encontrar el puerto del dongle

- **Windows:** Administrador de dispositivos → *Puertos (COM y LPT)*, o
  `python -m serial.tools.list_ports`. Se usa tal cual, p. ej. `COM11`.
- **Raspberry Pi:** `ls -l /dev/serial/by-id/`. El dongle es el de WCH (vendor `1a86`), no el GPS.
  Enumera como `/dev/ttyACM*`, no `/dev/ttyUSB*`, y el número puede cambiar al reiniciar si hay
  otro dispositivo USB conectado. Pon en `port` la ruta completa de `/dev/serial/by-id/...`, que no
  cambia.

### 3. Crear `config.toml`

```
cp config.example.toml config.toml
```

Luego edita solo lo que distingue a cada equipo:

| Campo              | Maestro          | Cada nodo                                     |
|--------------------|------------------|-----------------------------------------------|
| `[radio] port`     | `COM11` (el tuyo) | `/dev/serial/by-id/...`                       |
| `[radio] addr`     | `10`             | `20`, `21`, `22`... **distinta en cada nodo** |
| `[node] master_addr` | no se usa      | `10` (la `addr` del maestro)                  |

Estos campos de `[radio]` tienen que ser **idénticos en todos los equipos**: `channel`, `sf`,
`bw_code`, `cr`, `netid`, `lbt` y `rssi_append`. Si uno difiere, los módulos simplemente no se
oyen y ningún programa da error: solo hay silencio.

Dos nodos con la misma `addr` mezclan sus muestras en el maestro, que las identifica por esa
dirección. El resto de parámetros de `[node]` (ritmo de muestreo, timeouts, jitter ALOHA) están
comentados en `config.example.toml`.

`config.toml` es local por máquina y no se versiona (está en `.gitignore`).

### 4. Cargar la configuración al módulo y probar el enlace

Primero el maestro, que escucha:
```
python -m tools.hello_test recv config.toml 60
```

Cuando imprima `configuración aplicada`, en el nodo:
```
python -m tools.hello_test send config.toml 10 --repeat 5 --interval 3
```

(`10` es la `addr` del maestro.) Sin `--skip-config`, cada uno escribe la configuración en su
módulo y la relee para confirmarla. Tarda unos 4 s y durante ese tiempo la radio está sorda, por
eso el receptor va primero.

En el maestro debe aparecer cinco veces algo como `rx 6 B: b'hola34'`: los 4 bytes de `hola` más
2 bytes de RSSI que añade el módulo. Si llegan 5 B en vez de 6, el firmware se comporta distinto
al probado y hay que ajustar `RSSI_SUFFIX_LEN` en `common/frame.py` antes de seguir.

Si falla:

- `RadioError: ... no entró en modo AT`: desconecta y reconecta el dongle, y revisa `port`.
- `InvalidChannelError`: `channel` fuera de la banda legal colombiana (65-78).
- No llega nada: compara los campos de `[radio]` que deben ser idénticos y, en la Pi, la
  alimentación.
- Otras consultas al módulo a mano: `python -m tools.at_console <puerto>`.

## Puesta en marcha

Siempre el maestro primero (PC Windows):
```
python -m master.main config.toml
```

Después cada nodo (Raspberry Pi):
```
python -m node.main config.toml
```

Ambos aplican y verifican la configuración de radio al arrancar y corren indefinidamente.
`Ctrl+C` para detener. No hace falta borrar `node.db` ni `master.db` entre ejecuciones.

Para actualizar el código: `git pull` en la PC y en cada Pi, y volver a arrancar los programas.

El ritmo del nodo (cada cuánto mide, tiempos de espera, jitter) se ajusta en la sección `[node]`
de `config.toml`; cada parámetro está explicado en `config.example.toml`. Con varios nodos
transmitiendo a la vez deja `lbt = true` y `aloha_max_delay_s = 2.0`; con uno solo en pruebas
puedes bajar el jitter a `0.2` para ir más rápido.

## Cómo leer el log

**Nodo**, una línea por cada lote enviado:
```
[19:48:31] TX seq 1637-1646 (10 muestras), ACK hasta 1646, 172 pendientes
```
- `TX seq 1637-1646`: números de secuencia de las muestras que acaba de enviar. Cada muestra
  tiene uno propio, que nunca se repite.
- `ACK hasta 1646`: el maestro confirma que tiene **todas** las muestras seguidas hasta esa.
- `172 pendientes`: muestras que aún esperan confirmación. En marcha normal es `0`.
- `SIN ACK`: no llegó la confirmación (trama perdida, maestro apagado o fuera de alcance). No se
  pierde nada: las muestras siguen en cola y se reenvían. Si aparece suelto, es normal.
- `RESYNC: ...`: el nodo detectó que el maestro trae una numeración vieja (por ejemplo, se borró
  `node.db` o se cambió la Pi) y renumeró su cola. Se recupera solo, sin perder muestras.

**Maestro**, una línea por cada lote recibido:
```
[22:55:05] nodo 21: seq 393-402 (10 nuevas de 10), -26 dBm, ACK hasta 402
```
- `10 nuevas de 10`: cuántas no tenía ya. Menos de 10 significa que eran reenvíos, que se
  descartan sin duplicar nada en el CSV.
- `-26 dBm`: potencia de la señal recibida. Cuanto más cerca de 0, más fuerte.
- `trama descartada (...)`: llegó algo corrupto. Se ignora y el nodo lo reenvía.

**Tras un corte** (maestro apagado un rato) el nodo acumula pendientes. Al volver el enlace
alterna lotes viejos, en orden, con lotes de lo recién medido, para que los datos en vivo sigan
llegando mientras se vacía el atraso. Por eso el log salta entre dos secuencias:
```
TX seq 1637-1646 (10 muestras), ACK hasta 1646, 172 pendientes    <- atraso, en orden
TX seq 1808-1817 (10 muestras), ACK hasta 1646, 173 pendientes    <- lo más reciente
TX seq 1647-1656 (10 muestras), ACK hasta 1656, 164 pendientes    <- atraso, en orden
...
TX seq 1807-1816 (10 muestras), ACK hasta 1851, 1 pendientes
```
Con los lotes recientes el `ACK` no sube, porque al maestro todavía le falta el atraso. Cuando
este termina, el ACK salta de golpe (`hasta 1851`) porque las muestras recientes ya estaban
guardadas. Ese salto es correcto, no una pérdida.

## Qué genera

**Maestro:** `csv/telemetria-AAAA-MM-DD.csv` (uno por día, formato largo, cabecera fija). Es el
resultado principal del sistema:

```csv
ts_utc,ts_nodo,nodo_id,seq,variable,valor,rssi
2026-08-08T22:14:03.120Z,2026-08-08T22:14:03.008Z,20,84213,temp_c,24.71,-73
```

`ts_utc` es la llegada al maestro, `ts_nodo` la del sensor (para detectar deriva de reloj entre
ambos). `node/sampler.py` es un stub con datos sintéticos (`temp_c`, `volt`, `hum_pct`,
`press_hpa`); sustituir `read_sample()` por la lectura real de sensores cuando estén definidos,
sin tocar el resto del sistema.

**Cada nodo:** `node.db`, que es a la vez la cola de envío y el registro permanente de todo lo que
midió. Lo que el maestro confirma sale de la cola pero nunca se borra, así que sirve de respaldo
si después falla el maestro. Ocupa unos 50 B por muestra (~4 MB/día a 1 muestra/s). Cómo sacarlo,
en la sección siguiente.

## Recuperar el registro de un nodo (paso a paso)

**1. Exportarlo a CSV, en la Pi** (por SSH, dentro de la carpeta `lorasync`). Se puede hacer con
`node.main` corriendo, no lo interrumpe:
```
python -m tools.exportar_nodo node.db nodo.csv
```
Columnas: `ts_nodo,seq,variable,valor,confirmado`. Cada muestra ocupa 4 filas, una por variable.
`confirmado` es `1` si el maestro ya la tiene y `0` si todavía está pendiente.

**2. Echarle un vistazo en la Pi** (opcional):
```
head -20 nodo.csv                        # primeras filas
column -s, -t < nodo.csv | less -S       # como tabla; flechas para moverte, q para salir
wc -l nodo.csv                           # filas: (muestras × 4) + 1 de cabecera
awk -F, 'NR>1 {c[$5]++} END {for (k in c) print "confirmado="k, c[k]/4, "muestras"}' nodo.csv
```
La última línea cuenta cuántas muestras están confirmadas y cuántas pendientes.

**3. Copiarlo a la PC.** Este comando se ejecuta en PowerShell **en la PC**, no dentro de la
sesión SSH. Usa el mismo usuario e IP con los que entras por `ssh`:
```
scp cnvte6@192.168.137.71:~/lorasync/nodo.csv .
```
El punto final es obligatorio: significa "copiar aquí", a la carpeta donde estás en PowerShell.
Sin él, `scp` solo muestra su ayuda (`usage: scp ...`). Si recuperas varios nodos, dale a cada
copia un nombre distinto para que no se pisen:
```
scp cnvte8@<ip-de-esa-pi>:~/lorasync/nodo.csv nodo_cnvte8.csv
```
Los `.csv` no se suben a git (están en `.gitignore`).

**4. Abrirlo en Excel.** Si Excel lo muestra todo en una sola columna (pasa cuando Windows usa
coma decimal), ábrelo desde *Datos → Obtener datos → Desde texto/CSV* y elige la coma como
delimitador.

## Comprobar que no faltan datos

En la PC, desde la carpeta del proyecto (funciona en PowerShell). Revisa todos los CSV del maestro
y dice, por nodo, cuántos números de secuencia faltan y cuántas filas están duplicadas:
```
python -c "import csv,glob,collections as c;k=c.Counter((r['nodo_id'],int(r['seq']),r['variable']) for f in glob.glob('csv/telemetria-*.csv') for r in csv.DictReader(open(f,encoding='utf-8')));s=c.defaultdict(set);[s[n].add(q) for n,q,_ in k];[print('nodo',n,'seq',min(v),'-',max(v),'| faltan',max(v)-min(v)+1-len(v),'| duplicadas',sum(x>1 for (m,_,_),x in k.items() if m==n)) for n,v in sorted(s.items())]"
```
Resultado correcto:
```
nodo 20 seq 1 - 1598 | faltan 0 | duplicadas 0
nodo 22 seq 1 - 3867 | faltan 0 | duplicadas 0
```

## Qué pasa si algo falla

| Situación | Qué hace el sistema | Qué tienes que hacer |
|---|---|---|
| Se apaga o cae el maestro | Los nodos siguen midiendo y acumulan la cola en `node.db` | Volver a arrancar `master.main`; el atraso se vacía solo |
| Se pierden tramas o ACKs sueltos | `SIN ACK` y reenvío automático | Nada |
| Un nodo pierde la corriente | La cola sobrevive en `node.db` | Volver a arrancar `node.main` |
| Se borra `node.db` o se cambia la Pi | El nodo detecta la numeración vieja del maestro (`RESYNC`) y renumera su cola | Nada |
| El nodo queda lejos o con mala señal | `SIN ACK` intermitentes; la cola crece y luego se vacía | Nada, o acercarlo |
| Se pierde `master.db` en la PC | Los datos se siguen guardando en el CSV, pero los nodos ya no reciben confirmación y su cola crece sin vaciarse | **No borres `master.db`.** Si se pierde, restáuralo de una copia. Es una limitación conocida (ver `PLAN.md`, Fase 4) |

Nunca desconectes la antena de un dongle mientras transmite: la potencia reflejada puede dañar el
amplificador del módulo. Para simular mala señal, aleja el nodo o baja `power_dbm`.

## Pruebas con hardware (paso a paso)

Así se verificó la Fase 3. Sirve para comprobar una instalación nueva:

1. Arranca el maestro y 2 nodos (`addr` distintas). El CSV del maestro debe crecer con datos de
   ambos.
2. Detén el maestro con `Ctrl+C` durante 10 minutos con los nodos corriendo. Sus pendientes
   deben subir.
3. Vuelve a arrancarlo. Mientras se vacía el atraso, el log del maestro debe mostrar de vez en
   cuando lotes con los seq más recientes intercalados con los viejos.
4. Espera a que los nodos vuelvan a `0 pendientes` y ejecuta la comprobación de
   [Comprobar que no faltan datos](#comprobar-que-no-faltan-datos): `faltan 0` y `duplicadas 0`.
5. Repite alejando un nodo hasta el límite de alcance (en vez de quitarle la antena) y
   acercándolo de nuevo.
6. Repite desenchufando la fuente de una Pi mientras corre (no con `Ctrl+C`). Al encenderla,
   `node.main` debe arrancar sin errores y no debe faltar nada.
7. En una Pi, exporta `node.db` y comprueba que las muestras confirmadas coinciden con las del CSV
   del maestro para ese nodo.

## Verificación sin hardware

```
pytest tests/
```

`tests/test_e2e_simulado.py`, `tests/test_drenado.py` y `tests/test_node_resync.py` corren nodo y
maestro juntos (hilos separados, puertos serie falsos que imitan al dongle real), incluyendo cortes
del maestro, tramas perdidas y reinicios del nodo, y comprueban que no falta ni se duplica ninguna
muestra.
