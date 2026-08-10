# lorasync

Telemetría por LoRa: varias Raspberry Pi Zero 2W ("nodos") envían muestras a 1 Hz a un PC
maestro Windows por dongles Waveshare USB-TO-LoRa-HF (SX1262). El maestro escribe todo a CSV.
Diseño y fundamentos completos en `docs/fundamentos-lora.pdf` (compilar con `docs/Makefile`).
Detalles de arquitectura, decisiones y trampas de hardware ya resueltas: ver `PLAN.md`.

## Instalación

```
pip install pyserial
```

Todo lo demás es biblioteca estándar de Python 3.11+ (`tomllib`, `sqlite3`, `csv`, `struct`).

## Configuración

Cada máquina (cada nodo y el maestro) necesita su propio `config.toml`, copiado de
`config.example.toml`:

```
[radio]
port = "COM3"          # o "/dev/ttyACM0" en la Pi
addr = 20               # única por dongle: 10 para el maestro, 20/21/... para cada nodo
channel = 70             # 920 MHz, no tocar salvo laboratorio (banda legal CO)
lbt = true                # true en ALOHA (Fase 3), false en TDMA (Fase 4)

[node]                   # solo para node/main.py
master_addr = 10          # addr del dongle del maestro
db_path = "node.db"

[master]                 # solo para master/main.py
db_path = "master.db"
csv_dir = "csv"
```

`config.toml` es local por máquina y no se versiona (está en `.gitignore`).

## Puesta en marcha

Maestro (PC Windows):
```
python -m master.main config.toml
```

Cada nodo (Raspberry Pi):
```
python -m node.main config.toml
```

Ambos aplican la configuración de radio al arrancar (`AT+SF`, `AT+TXCH`, etc., releída y
confirmada) y corren indefinidamente. `Ctrl+C` para detener; en el nodo, la cola pendiente en
`node.db` sobrevive al corte y se reenvía al reiniciar (no hace falta drenarla a mano).

Diagnóstico manual del dongle: `python -m tools.at_console [puerto]`.

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
