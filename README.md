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
`Ctrl+C` para detener. En el nodo, la cola pendiente en `node.db` sobrevive a cortes de luz y se
reenvía al volver. No hace falta borrar `node.db` ni `master.db` entre ejecuciones: si un nodo
arranca con un `node.db` nuevo contra un maestro que conserva su historial, el nodo lo detecta y
renumera su cola por encima sin perder muestras (`RESYNC` en el log).

## Qué genera

El maestro escribe `csv/telemetria-AAAA-MM-DD.csv` (uno por día, formato largo, cabecera fija):

```csv
ts_utc,ts_nodo,nodo_id,seq,variable,valor,rssi
2026-08-08T22:14:03.120Z,2026-08-08T22:14:03.008Z,20,84213,temp_c,24.71,-73
```

`ts_utc` es la llegada al maestro, `ts_nodo` la del sensor (para detectar deriva de reloj entre
ambos). `node/sampler.py` es un stub con datos sintéticos (`temp_c`, `volt`, `hum_pct`,
`press_hpa`); sustituir `read_sample()` por la lectura real de sensores cuando estén definidos,
sin tocar el resto del sistema.

## Verificación

```
pytest tests/
```

`tests/test_e2e_simulado.py` corre el ciclo completo (nodo + maestro, hilos separados, puertos
serie falsos) sin hardware. La prueba que de verdad importa es con hardware real: arrancar
maestro y nodos, apagar el maestro varios minutos con los nodos muestreando, reencenderlo y
comprobar que el CSV queda con secuencias contiguas por nodo, sin huecos ni duplicados (criterio
completo en `PLAN.md`, Fase 3).
