# AutoResearch Polymarket - Guia Tecnica Completa

## Indice

1. [Que es AutoResearch](#1-que-es-autoresearch)
2. [Que es Binary Arbitrage](#2-que-es-binary-arbitrage)
3. [Arquitectura del Sistema](#3-arquitectura-del-sistema)
4. [El Bucle Principal - Como Funciona](#4-el-bucle-principal)
5. [Cada Fichero Explicado](#5-cada-fichero-explicado)
6. [El Modelo de IA - Cuando y Como Actua](#6-el-modelo-de-ia)
7. [La Metrica RAPR](#7-la-metrica-rapr)
8. [Rigor Estadistico - Welch's t-test](#8-rigor-estadistico)
9. [El Paper Trader - Simulacion Realista](#9-el-paper-trader)
10. [El Dashboard](#10-el-dashboard)
11. [Como Ejecutarlo](#11-como-ejecutarlo)
12. [Flujo Completo Paso a Paso](#12-flujo-completo-paso-a-paso)
13. [FAQ y Troubleshooting](#13-faq-y-troubleshooting)

---

## 1. Que es AutoResearch

### El Concepto Original (Karpathy)

En febrero de 2025, Andrej Karpathy (ex-director de IA en Tesla, cofundador de OpenAI) publico AutoResearch: un sistema donde un agente de IA investiga de forma completamente autonoma. La idea es radical:

```
BUCLE INFINITO:
  1. El agente tiene una HIPOTESIS ("si cambio X, mejorara Y")
  2. Modifica el CODIGO para probar su hipotesis
  3. EJECUTA el experimento y recoge DATOS
  4. EVALUA estadisticamente si mejoro
  5. Si mejoro -> KEEP (mantener cambio)
     Si no   -> DISCARD (descartar, volver atras)
  6. REPETIR con nueva hipotesis
```

El original de Karpathy optimizaba un modelo de lenguaje (LLM). La metrica era `val_bpb` (bits per byte en validacion). El agente modificaba hiperparametros de entrenamiento, arquitectura, y evaluaba si la metrica mejoraba.

### Nuestra Adaptacion

Nosotros aplicamos exactamente el mismo concepto, pero en lugar de optimizar un LLM, **optimizamos una estrategia de trading**:

| Aspecto | Karpathy Original | Nuestra Version |
|---------|-------------------|-----------------|
| **Que optimiza** | Modelo de lenguaje | Estrategia de trading |
| **Metrica** | val_bpb (bits per byte) | RAPR (Risk-Adjusted Profit Rate) |
| **Que modifica** | Hiperparametros de training | Parametros y logica de `strategy.py` |
| **Entorno** | GPU training runs | Mercados reales de Polymarket (paper) |
| **Evaluacion** | Val loss comparisons | Welch's t-test + RAPR |
| **Duracion/exp** | ~30 min training | ~65 min (30 baseline + 30 test + 5 eval) |
| **Datos** | Dataset fijo | Mercado en vivo (5 coins, cada 30s) |

La filosofia es identica: **un agente autonomo que experimenta, evalua, y evoluciona su propia estrategia sin intervencion humana**.

---

## 2. Que es Binary Arbitrage

### La Unica Estrategia

Este sistema trabaja con UNA sola estrategia: Binary Arbitrage en los mercados "Up or Down" de 5 minutos de Polymarket.

### Como Funciona Polymarket

Polymarket es un mercado de prediccion. Para cada pregunta ("Will BTC go up in the next 5 minutes?"), existen dos tokens:

```
TOKEN YES (Up)   -> Paga $1.00 si BTC sube
TOKEN NO (Down)  -> Paga $1.00 si BTC baja
```

Siempre uno gana y otro pierde. Al resolverse el mercado, el ganador vale $1.00 y el perdedor $0.00.

### El Arbitraje

```
Ejemplo concreto:

  Precio YES (Up):   $0.48
  Precio NO (Down):  $0.50
  ─────────────────────────
  TOTAL:             $0.98

  Si compramos AMBOS:
    - Pagamos: $0.98
    - Al resolver (5 min): recibimos $1.00 (siempre, no importa que pase)
    - Profit bruto: $0.02 (2 centavos por cada par)

  Pero hay fees:
    - Fee YES:  0.48 * (1-0.48) * 0.022 = $0.00549
    - Fee NO:   0.50 * (1-0.50) * 0.022 = $0.00550
    - Total fees: ~$0.011
    - Gas (Polygon): ~$0.01

  Profit neto: $0.02 - $0.011 - $0.01 = ~$0.009 por par

  Con $10 de inversion:
    - Shares: 10 / 0.98 = 10.2 shares
    - Payout: 10.2 * $1.00 = $10.20
    - Profit neto: ~$0.09 (~0.9% en 5 minutos)
```

### Por que funciona

El "gap" (diferencia entre $1.00 y el coste combinado) existe porque:
- Los market makers no son perfectos
- Hay latencia entre las dos caras del mercado
- El orderbook tiene spreads
- En mercados de 5 min, la liquidez es limitada

**El gap debe superar los fees totales para que sea rentable.**

### Formula de Fees de Polymarket

```
fee_por_lado = precio * (1 - precio) * 0.022

La fee es MAXIMA cuando precio = 0.50:
  0.50 * 0.50 * 0.022 = 0.0055 = 0.55%

Para un round-trip (comprar YES + NO):
  Fee total ≈ 1.1% cuando ambos estan cerca de $0.50

Por lo tanto, necesitas un gap > ~1.5% para cubrir fees + slippage + gas.
```

### Las 5 Monedas

El sistema monitorea exactamente 5 criptomonedas:

| Moneda | Par Binance | Volatilidad Tipica | Mercados |
|--------|-------------|-------------------|----------|
| **BTC** | BTCUSDT | ~1-3% diario | Bitcoin Up or Down |
| **ETH** | ETHUSDT | ~2-4% diario | Ethereum Up or Down |
| **SOL** | SOLUSDT | ~3-5% diario | Solana Up or Down |
| **XRP** | XRPUSDT | ~3-5% diario | XRP Up or Down |
| **DOGE** | DOGEUSDT | ~4-6% diario | Dogecoin Up or Down |

Cada moneda tiene mercados "Up or Down" de 5 minutos que se crean y resuelven continuamente durante el horario de trading de EEUU (~9:30 AM - 6:00 PM ET).

---

## 3. Arquitectura del Sistema

### Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────────────────┐
│                      AUTORESEARCH POLYMARKET                         │
│                                                                      │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐     │
│  │              │     │              │     │                  │     │
│  │  Polymarket  │────>│   market_    │────>│                  │     │
│  │  CLOB API    │     │  fetcher.py  │     │                  │     │
│  │              │     │              │     │                  │     │
│  └──────────────┘     └──────────────┘     │                  │     │
│                                             │  orchestrator.py │     │
│  ┌──────────────┐     ┌──────────────┐     │  (MAIN LOOP)     │     │
│  │              │     │              │     │                  │     │
│  │   Binance    │────>│   binance    │────>│  Cada 30 seg:    │     │
│  │     API      │     │   prices     │     │  poll -> decide  │     │
│  │              │     │              │     │  -> trade -> log  │     │
│  └──────────────┘     └──────────────┘     │                  │     │
│                                             └────────┬─────────┘     │
│                                                      │               │
│                    ┌─────────────────────────────────┼───────────┐   │
│                    │                                 │           │   │
│              ┌─────▼──────┐  ┌──────────────┐  ┌────▼────────┐ │   │
│              │            │  │              │  │             │ │   │
│              │ strategy.py│  │ paper_trader │  │ experiment_ │ │   │
│              │            │  │    .py       │  │ manager.py  │ │   │
│              │ EL AGENTE  │  │              │  │             │ │   │
│              │ MODIFICA   │  │ Simula       │  │ Lifecycle   │ │   │
│              │ ESTE FILE  │  │ trades con   │  │ de cada     │ │   │
│              │            │  │ realismo     │  │ experimento │ │   │
│              │ decide()   │  │              │  │             │ │   │
│              └────────────┘  └──────────────┘  └─────────────┘ │   │
│                    │                                 │           │   │
│                    └─────────────────────────────────┘           │   │
│                                      │                           │   │
│                              ┌───────▼────────┐                  │   │
│                              │                │                  │   │
│                              │   scorer.py    │                  │   │
│                              │                │                  │   │
│                              │ RAPR + t-test  │                  │   │
│                              │ keep/discard   │                  │   │
│                              └───────┬────────┘                  │   │
│                                      │                           │   │
│                              ┌───────▼────────┐                  │   │
│                              │                │                  │   │
│                              │    db.py       │                  │   │
│                              │  SQLite DB     │                  │   │
│                              │ research.db    │                  │   │
│                              └───────┬────────┘                  │   │
│                                      │                           │   │
│                              ┌───────▼────────┐  ┌────────────┐ │   │
│                              │                │  │            │ │   │
│                              │  server.py     │  │ dashboard/ │ │   │
│                              │  HTTP :8080    │──│ index.html │ │   │
│                              │  /api/data     │  │ app.js     │ │   │
│                              │                │  │ style.css  │ │   │
│                              └────────────────┘  └────────────┘ │   │
│                                                                  │   │
└──────────────────────────────────────────────────────────────────┘   │
```

### Diagrama de Flujo de Datos

```
APIS EXTERNAS                    SISTEMA                         SALIDA
─────────────                    ───────                         ──────

Polymarket ──── orderbook ──┐
  CLOB API     yes/no asks  │
                            ├──> poll_all_coins() ──> observations[]
Binance ────── price ───────┘         │
  REST API     volatility             │
                                      ▼
                              strategy.decide()
                                      │
                                      ▼
                              decisions[] ──> paper_trader
                                                  │
                                                  ▼
                              trades[] ──> DB (polls, trades, portfolio)
                                                  │
                                                  ▼
                              scorer.py ──> RAPR, t-test, keep/discard
                                                  │
                                                  ▼
                              results.tsv + experiments table
                                                  │
                                                  ▼
                              server.py ──> JSON API ──> Dashboard
```

### Estructura de Ficheros

```
C:\Proyectos_ignasi\autoresearch_polymarket\
│
├── orchestrator.py          # EL CEREBRO: bucle principal autonomo
├── market_fetcher.py        # Datos en vivo de Polymarket + Binance
├── strategy.py              # LA UNICA VARIABLE: lo que el agente modifica
├── strategy_default.py      # Backup de la estrategia original (safety net)
├── paper_trader.py          # Simulacion realista de trades
├── scorer.py                # Metrica RAPR + Welch's t-test
├── experiment_manager.py    # Ciclo de vida de experimentos
├── db.py                    # Base de datos SQLite
├── server.py                # Servidor HTTP para el dashboard
├── program.md               # Playbook de investigacion (guia al agente)
├── results.tsv              # Log tabular de todos los experimentos
│
├── dashboard/
│   ├── index.html           # Dashboard web (4 tabs)
│   ├── app.js               # Logica de renderizado + Chart.js
│   └── style.css            # Dark theme CSS
│
└── data/
    ├── research.db          # SQLite database
    ├── dashboard_data.json  # JSON export para dashboard
    ├── strategy_versions/   # Snapshots de cada version de strategy.py
    └── reports/             # Reportes de ciclo
```

---

## 4. El Bucle Principal

### Vision General

```
    INICIO
      │
      ▼
 ┌──────────┐
 │  init_db  │  Crear tablas SQLite
 │  init_tsv │  Crear results.tsv
 └────┬──────┘
      │
      ▼
 ┌──────────────┐
 │  DISCOVER     │  Buscar mercados "Up or Down" para las 5 monedas
 │  MARKETS      │  via Polymarket Gamma API
 └────┬──────────┘
      │
      ▼
 ┌──────────────┐
 │  FASE 0:      │  5 min de observacion pura
 │  OBSERVE      │  Verificar que las APIs funcionan
 └────┬──────────┘
      │
      ▼
 ┌──────────────────────────────────────────────────────┐
 │                                                       │
 │              BUCLE INFINITO DE EXPERIMENTACION         │
 │                                                       │
 │  ┌──────────────────┐                                 │
 │  │  PASO 1:          │                                │
 │  │  BASELINE          │  30 min con la estrategia     │
 │  │  (30 min)          │  ACTUAL sin cambios           │
 │  └────────┬───────────┘                                │
 │           │                                            │
 │           ▼                                            │
 │  ┌──────────────────┐                                 │
 │  │  PASO 2:          │  Seleccionar un parametro,     │
 │  │  MUTACION          │  cambiar su valor en           │
 │  │                    │  strategy.py                   │
 │  └────────┬───────────┘                                │
 │           │                                            │
 │           ▼                                            │
 │  ┌──────────────────┐                                 │
 │  │  PASO 3:          │  Hot-reload strategy.py,       │
 │  │  TEST              │  30 min con la estrategia     │
 │  │  (30 min)          │  MODIFICADA                   │
 │  └────────┬───────────┘                                │
 │           │                                            │
 │           ▼                                            │
 │  ┌──────────────────┐                                 │
 │  │  PASO 4:          │  Calcular RAPR de ambos,       │
 │  │  EVALUAR           │  Welch t-test, p-value        │
 │  └────────┬───────────┘                                │
 │           │                                            │
 │           ▼                                            │
 │  ┌──────────────────────────────────────────┐         │
 │  │  PASO 5: DECIDIR                          │         │
 │  │                                            │         │
 │  │  RAPR mejora >5% AND p < 0.10?            │         │
 │  │  ├─ SI ──────────> KEEP                    │         │
 │  │  │                 (guardar cambio,         │         │
 │  │  │                  strategy_default = new) │         │
 │  │  │                                          │         │
 │  │  │  RAPR mejora >30% AND p < 0.01?         │         │
 │  │  │  ├─ SI ──────> CONFIRM                   │         │
 │  │  │  │             (repetir test 1 vez)      │         │
 │  │  │  │                                       │         │
 │  │  ├─ NO ─────────> DISCARD                   │         │
 │  │  │                 (revert strategy.py,      │         │
 │  │  │                  git reset)               │         │
 │  └──┴──────────────────────────────────────────┘         │
 │           │                                            │
 │           ▼                                            │
 │  ┌──────────────────┐                                 │
 │  │  COOLDOWN         │                                │
 │  │  (5 min)          │  Descanso entre experimentos   │
 │  └────────┬───────────┘                                │
 │           │                                            │
 │           └──────────> REPETIR DESDE PASO 1            │
 │                                                       │
 └──────────────────────────────────────────────────────┘
```

### Tiempos

```
Un experimento completo:

  BASELINE:    30 min ──────────────────────────────┐
  MUTACION:     ~1 seg                              │
  TEST:        30 min ──────────────────────────────┤  ~65 min
  EVALUACION:   ~1 seg                              │
  COOLDOWN:     5 min ─────────────────────────────┘

  En 8 horas: ~7 experimentos
  En 24 horas: ~22 experimentos

Datos por experimento:
  - 5 monedas x 2 polls/min x 30 min = 300 polls por fase
  - 600 polls totales (baseline + test)
  - Cada poll = 5 observaciones (una por moneda)
  - Total: ~3000 puntos de datos por experimento
```

### El Codigo del Bucle (orchestrator.py:main)

```python
def main():
    # 1. Inicializar todo
    init_db()                           # Crear tablas SQLite
    init_results_tsv()                  # Crear results.tsv con headers
    trader = RealisticPaperTrader()     # Simulador con $1000
    manager = ExperimentManager()       # Gestor de ciclo de vida

    # 2. Descubrir mercados
    markets = market_fetcher.discover_markets()  # Busca "Up or Down" x5

    # 3. Fase 0: observacion (5 min)
    run_phase("observe", 5, trader)     # Verifica que todo funciona

    # 4. BUCLE INFINITO
    experiment_num = 0
    while True:
        experiment_num += 1

        # Paso 1: BASELINE (30 min con estrategia actual)
        baseline_trades = run_phase("baseline", 30, trader)

        # Paso 2: MUTACION (cambiar un parametro en strategy.py)
        hypothesis = _propose_mutation(experiment_num)
        exp = manager.create_experiment(hypothesis)
        manager.start_experiment(exp)

        # Paso 3: TEST (30 min con estrategia modificada)
        manager.transition_to_test(exp)  # hot-reload strategy.py
        test_trades = run_phase("test", 30, trader)

        # Paso 4: EVALUAR (RAPR + t-test)
        result = manager.evaluate_experiment(exp, baseline_trades, test_trades, 0.5, 0.5)

        # Paso 5: DECIDIR
        keep = result.get("keep", False)
        if result["result"] == "confirm_needed":
            confirm_trades = run_phase("confirm", 30, trader)
            # ... re-evaluate
        manager.finalize(exp, keep)  # keep -> avanzar, discard -> revert

        # Cooldown
        time.sleep(300)  # 5 minutos
```

### El Polling Loop (run_phase)

Dentro de cada fase (baseline o test), el sistema hace un poll cada 30 segundos:

```python
def run_phase(phase_name, duration_mins, trader):
    all_trades = []
    start = time.time()

    while time.time() < start + duration_mins * 60:
        # 1. POLL: Obtener datos de las 5 monedas en paralelo
        observations = market_fetcher.poll_all_coins()
        #   -> Para cada moneda:
        #      - Orderbook completo (YES y NO) via CLOB API
        #      - Precio de Binance
        #      - Volatilidad 24h

        # 2. GUARDAR: Cada observacion va a la DB
        save_to_polls_table(observations)

        # 3. DECIDIR: La estrategia analiza y decide
        decisions = strategy.decide(observations, all_trades, config)
        #   -> Devuelve lista de trades a ejecutar (o vacia)

        # 4. EJECUTAR: Paper trader simula cada trade
        for decision in decisions:
            trade = trader.execute_binary_arb(decision, observation)
            #   -> Camina el orderbook real
            #   -> Calcula slippage, fees, fill probability
            #   -> Simula si se llena o no

        # 5. RESOLVER: Trades que ya cerraron (window de 5 min)
        trader.resolve_trades()

        # 6. ESPERAR 30 segundos
        time.sleep(30)

    return all_trades
```

---

## 5. Cada Fichero Explicado

### `orchestrator.py` - El Cerebro (430 lineas)

**Que hace**: Es el programa principal. Orquesta todo: descubrimiento de mercados, polling, ejecucion de la estrategia, ciclo de experimentos, y exportacion de datos.

**Funciones clave**:
- `main()` - El bucle infinito de experimentacion
- `run_phase(name, mins, trader)` - Ejecuta una fase de polling (baseline/test)
- `_propose_mutation(exp_num)` - Sistema de mutacion autonoma de parametros
- `export_dashboard_data()` - Exporta JSON para el dashboard

**Constantes**:
```python
POLL_INTERVAL_SECS = 30      # Frecuencia de polling
PHASE_DURATION_MINS = 30     # Duracion de cada fase
COOLDOWN_MINS = 5            # Pausa entre experimentos
OBSERVE_MINS = 5             # Observacion inicial
MIN_TRADES_TO_EVALUATE = 3   # Minimo de trades para evaluar
```

**Sistema de Mutacion**:
Cuando el sistema opera de forma autonoma (sin Claude Code), muta parametros aleatoriamente:
```python
MUTATIONS = [
    ("MIN_GAP_CENTS", [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0]),
    ("MAX_SPREAD", [0.02, 0.03, 0.04, 0.05, 0.08, 0.10]),
    ("MIN_DEPTH_USD", [10, 25, 50, 100, 200]),
    ("ORDER_SIZE_USD", [5, 10, 15, 20, 50]),
    ("MAX_TOTAL_COST", [0.98, 0.985, 0.99, 0.995, 0.998]),
    ("MAX_TRADES_PER_POLL", [1, 2, 3, 5]),
]
```

---

### `market_fetcher.py` - Los Ojos (320 lineas)

**Que hace**: Conecta con las APIs publicas de Polymarket y Binance para obtener datos en tiempo real de las 5 monedas.

**APIs que usa**:
```
Polymarket Gamma API (descubrimiento):
  GET https://gamma-api.polymarket.com/markets
  -> Lista de mercados activos con condition_id, token_ids, outcomes

Polymarket CLOB API (orderbooks):
  GET https://clob.polymarket.com/book?token_id=XXX
  -> Orderbook completo: bids[], asks[] con price y size

  GET https://clob.polymarket.com/midpoint?token_id=XXX  (fallback)
  GET https://clob.polymarket.com/spread?token_id=XXX    (fallback)

Binance API (precios de referencia):
  GET https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT
  GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=24
```

**Ninguna necesita API key** - son endpoints publicos.

**Funciones clave**:
- `discover_markets()` - Busca mercados "Up or Down" para BTC/ETH/SOL/XRP/DOGE. Ordena por `startDate` descendente, filtra expirados, y selecciona el mas proximo a vencer (mayor liquidez).
- `poll_all_coins()` - Fetch paralelo (ThreadPoolExecutor, 10 workers) de orderbooks YES y NO para las 5 monedas + precios Binance. Devuelve lista de observaciones.
- `get_realized_volatility(symbol)` - Volatilidad 24h desde klines de Binance (con cache de 10 min).
- `_fetch_orderbook(token_id)` - Orderbook completo con calculo de profundidad.

**Cache**:
- Mercados: re-descubre cada 2 minutos (los mercados de 5 min rotan rapido)
- Volatilidad: cache de 10 minutos (no cambia rapido)

---

### `strategy.py` - El Cerebro Mutable (103 lineas)

**Que hace**: Contiene la logica de decision de trading. Es el **UNICO fichero que el agente de IA modifica**.

**Contrato inmutable**: La funcion `decide()` siempre debe existir con esta firma:
```python
def decide(observations: list, history: list, config: dict) -> list:
```

**Parametros iniciales**:
```python
MIN_GAP_CENTS = 1.5       # Gap minimo en centavos (1.5%)
MAX_SPREAD = 0.04         # Spread maximo aceptable
MIN_DEPTH_USD = 50        # Profundidad minima del orderbook
ORDER_SIZE_USD = 10       # Tamano de cada trade
MAX_TOTAL_COST = 0.99     # Coste maximo YES+NO combinado
MAX_TRADES_PER_POLL = 2   # Max trades por poll de 30s
```

**Logica actual** (el agente puede reescribirla entera):
```
Para cada moneda observada:
  1. gap >= MIN_GAP_CENTS/100?       -> Si no, skip
  2. spread_yes <= MAX_SPREAD?       -> Si no, skip
  3. spread_no <= MAX_SPREAD?        -> Si no, skip
  4. depth_yes >= MIN_DEPTH_USD?     -> Si no, skip
  5. depth_no >= MIN_DEPTH_USD?      -> Si no, skip
  6. total_ask <= MAX_TOTAL_COST?    -> Si no, skip
  7. Todas pasan -> BUY_BOTH
```

**Lo que el agente puede hacer**:
- Cambiar cualquier parametro
- Anadir filtros nuevos (volatilidad, timing, momentum)
- Implementar analisis de orderbook (walls, imbalance)
- Usar senales cross-coin (si BTC tiene gap, ETH tambien?)
- Ajustar sizing dinamicamente
- Reescribir la funcion entera

---

### `paper_trader.py` - El Simulador (304 lineas)

**Que hace**: Simula trades con realismo extremo. No es un simple "compra al midpoint" - camina el orderbook real, calcula slippage, simula probabilidad de fill, y aplica fees exactos.

**Clase `RealisticPaperTrader`**:
```
Estado:
  - balance: $1000 inicial
  - total_pnl, total_trades, total_fees
  - winning/losing trades
  - pending_trades[] (esperando resolucion)
```

**Proceso de un trade** (`execute_binary_arb`):

```
  1. ORDERBOOK WALK
     ┌─────────────────────────────────────────────┐
     │ Para comprar $5 de YES tokens:               │
     │                                               │
     │   Nivel 1: 500 shares @ $0.48 -> compro 500  │
     │   Nivel 2: 300 shares @ $0.49 -> compro 300  │
     │   Nivel 3: 200 shares @ $0.50 -> compro 42   │
     │                                               │
     │   VWAP = (500*0.48 + 300*0.49 + 42*0.50)    │
     │        / (500 + 300 + 42)                     │
     │        = $0.4834                              │
     │                                               │
     │   (Peor que el "best ask" de $0.48)           │
     └─────────────────────────────────────────────┘

  2. SLIPPAGE
     slippage = min((order_size / depth)^2 * 0.5, 0.02)
     -> Ordenes pequenas en libros profundos: ~0.1%
     -> Ordenes grandes en libros finos: hasta 2%

  3. FILL PROBABILITY
     prob = spread_factor * depth_factor
     spread_factor = max(0.3, 1.0 - spread * 10)
     depth_factor = min(1.0, depth / (size * 3))
     -> Spread=0.01, depth bueno: ~90% fill
     -> Spread=0.05, depth malo: ~30% fill
     -> Si random() > prob: trade NO se ejecuta

  4. LATENCIA
     -> Simula 200-500ms de delay
     -> El precio puede moverse 0-0.2% en ese tiempo

  5. FEES EXACTOS
     fee = price * (1-price) * 0.022 * num_shares
     + gas: $0.005 * 2 (dos transacciones en Polygon)

  6. CALCULO FINAL
     total_cost = fill_yes + fill_no (por share)
     shares = size_usd / total_cost
     payout = shares * $1.00
     net_pnl = payout - size_usd - fees - slippage
     -> Solo se ejecuta si total_cost < max_total_cost
```

---

### `scorer.py` - El Juez (227 lineas)

**Que hace**: Evalua estadisticamente si un cambio en la estrategia mejoro o empeoro el rendimiento.

**Funciones**:
- `calculate_rapr()` - La metrica unica (ver seccion 7)
- `welch_ttest()` - Test estadistico puro Python (ver seccion 8)
- `compare_experiments()` - Decision completa keep/discard
- `format_comparison()` - Reporte legible

**Logica de decision**:
```python
if improvement > 5% AND p_value < 0.10:
    if improvement > 30% AND p_value < 0.01:
        return "confirm_needed"   # Demasiado bueno, repetir
    return "improved" -> KEEP

elif improvement > 0% AND p_value < 0.05:
    return "improved" -> KEEP     # Marginal pero significativo

else:
    return "no_improvement" -> DISCARD
```

---

### `experiment_manager.py` - El Archivero (300 lineas)

**Que hace**: Gestiona el ciclo de vida completo de cada experimento, incluyendo snapshots de codigo, hot-reload, y operaciones git.

**Ciclo de vida**:
```
PROPOSED -> BASELINE -> RUNNING -> COMPLETED/REVERTED/CRASHED
```

**Funciones clave**:
- `create_experiment(hypothesis)` - Crea registro en DB con estado "proposed"
- `start_experiment(exp)` - Transiciona a "baseline", guarda snapshot de strategy.py
- `transition_to_test(exp)` - Hot-reload de strategy.py modificado
  ```python
  importlib.reload(strategy)  # Recarga el modulo en memoria
  assert hasattr(strategy, "decide")  # Verifica que funciona
  # Si falla -> revert automatico a strategy_default.py
  ```
- `evaluate_experiment(exp, ...)` - Llama a scorer, actualiza DB, escribe results.tsv
- `finalize(exp, keep)`:
  - Si `keep=True`: copia strategy.py -> strategy_default.py (nueva base)
  - Si `keep=False`: copia strategy_default.py -> strategy.py (revert) + git reset
- `abort_experiment(exp, reason)` - Para crashes (auto-revert)

**Git integration**:
- Cada cambio hace `git commit -m "exp-N: hypothesis"`
- Cada revert hace `git reset HEAD~1 --hard`
- El historial git es la "memoria" de todos los cambios intentados

---

### `db.py` - La Memoria (168 lineas)

**Que hace**: Define y crea la base de datos SQLite con todas las tablas.

**Tablas**:

```sql
markets         -- Las 5 monedas y sus token IDs
  coin, condition_id, question, token_id_yes, token_id_no, end_date

polls           -- CADA observacion cada 30 seg (el dato crudo)
  coin, yes_bid, yes_ask, no_bid, no_ask, spread_yes, spread_no,
  total_ask, gap, depth_yes_usd, depth_no_usd, binance_price,
  volatility_1h, experiment_id, phase

trades          -- Cada trade simulado
  experiment_id, phase, coin, size_usd, fill_yes, fill_no,
  total_cost, fees, slippage, net_pnl, filled, reason

experiments     -- Historial de experimentos
  hypothesis, strategy_code, strategy_hash, status,
  baseline_rapr, test_rapr, p_value, improvement_pct, result

strategy_versions  -- Snapshots de cada version de strategy.py
  experiment_id, code, code_hash, description

portfolio       -- Historial de balance
  balance_usd, total_pnl, total_trades, total_fees,
  winning_trades, losing_trades
```

**Ubicacion**: `data/research.db` (SQLite, WAL journal mode para rendimiento)

---

### `server.py` - La Ventana (134 lineas)

**Que hace**: Servidor HTTP que sirve el dashboard y proporciona una API JSON.

**Endpoints**:
```
GET /           -> dashboard/index.html
GET /style.css  -> dashboard/style.css
GET /app.js     -> dashboard/app.js
GET /api/data   -> JSON con TODOS los datos del sistema
```

**El JSON de `/api/data`** incluye:
```json
{
  "generated_at": "2026-03-28T23:15:39",
  "polls": [...],              // Ultimas 100 observaciones
  "latest_per_coin": {...},    // Ultimo poll por moneda
  "trades": [...],             // Ultimos 100 trades
  "portfolio": [...],          // Historial de balance
  "experiments": [...],        // Todos los experimentos
  "markets": [...],            // Mercados activos
  "experiment_stats": {...},   // Contadores totales
  "strategy_code": "...",      // Codigo actual de strategy.py
  "results_tsv": "..."         // Contenido de results.tsv
}
```

---

## 6. El Modelo de IA - Cuando y Como Actua

### Dos Modos de Operacion

El sistema tiene **dos modos** de operacion, dependiendo de si hay un agente de IA supervisando:

#### Modo 1: Autonomo (sin IA - `python orchestrator.py`)

```
┌───────────────────────────────────────────────────────┐
│                  MODO AUTONOMO                         │
│                                                        │
│  El orchestrator se ejecuta solo.                      │
│  La "IA" es un sistema de mutacion simple:             │
│                                                        │
│  _propose_mutation():                                  │
│    1. Elige un parametro aleatorio de MUTATIONS[]      │
│    2. Elige un valor aleatorio de la lista             │
│    3. Reemplaza el valor en strategy.py                │
│    4. Devuelve hipotesis: "Change X from A to B"       │
│                                                        │
│  Es basicamante un grid search aleatorizado.           │
│  Util para exploracion inicial de parametros.          │
│  NO cambia logica, solo valores numericos.             │
│                                                        │
│  Ventaja: funciona 24/7 sin coste de API               │
│  Limitacion: solo optimiza parametros, no logica       │
└───────────────────────────────────────────────────────┘
```

#### Modo 2: Con Claude Code (el modo "Karpathy completo")

```
┌───────────────────────────────────────────────────────┐
│              MODO CON CLAUDE CODE                       │
│                                                        │
│  Claude Code (el agente de IA) es quien ejecuta todo.  │
│                                                        │
│  1. Claude lee program.md para entender las reglas     │
│  2. Claude lee results.tsv para ver que se ha probado  │
│  3. Claude lee strategy.py actual                      │
│  4. Claude PIENSA una hipotesis inteligente:           │
│     - "El MIN_GAP es muy alto, hay oportunidades       │
│       que se pierden. Bajarlo a 1.0 deberia            │
│       aumentar el numero de trades sin sacrificar      │
│       mucho la calidad."                               │
│  5. Claude MODIFICA strategy.py directamente           │
│     (puede cambiar logica, no solo parametros!)        │
│  6. Claude ejecuta: python orchestrator.py             │
│     (o llama a run_phase directamente)                 │
│  7. Claude ANALIZA los resultados                      │
│  8. Claude DECIDE si mantener o descartar              │
│  9. Claude PROPONE el siguiente experimento            │
│                                                        │
│  Ventaja: creatividad ilimitada, cambia logica,        │
│           entiende el contexto del mercado              │
│  Limitacion: coste de API de Claude, necesita          │
│              que la sesion de Claude Code este activa   │
└───────────────────────────────────────────────────────┘
```

### Como Conectar Claude Code

Para usar el modo Karpathy completo:

1. Abrir Claude Code en el directorio del proyecto
2. Claude Code leera `program.md` automaticamente (esta en el directorio)
3. Darle una instruccion como:

```
Lee program.md y results.tsv. Analiza el estado actual del sistema.
Propone y ejecuta el siguiente experimento siguiendo las reglas de
AutoResearch. Modifica strategy.py con tu hipotesis, ejecuta el
baseline y test, y decide si mantener o descartar.
```

Claude Code entonces:
- Leera `program.md` para entender las reglas
- Leera `results.tsv` para ver el historial
- Leera `strategy.py` para ver el estado actual
- Formulara una hipotesis
- Editara `strategy.py` con la modificacion
- Ejecutara el ciclo de experimento
- Analizara los resultados
- Decidira keep/discard

### La "Inteligencia" del Sistema

```
                    MODOS DE INTELIGENCIA
                    ─────────────────────

  MODO AUTONOMO                    MODO CLAUDE CODE
  ─────────────                    ────────────────

  Random param sweep               Analisis inteligente
       │                                │
       ▼                                ▼
  "Cambiar MIN_GAP                "He visto que los trades
   de 1.5 a 2.0"                   en SOL tienen peor fill
                                    rate que BTC. Voy a
                                    anadir un filtro de
                                    volatilidad que excluya
                                    coins con vol > 5% para
                                    reducir el slippage."

       │                                │
       ▼                                ▼
  Modifica 1 numero               Puede reescribir toda
  en strategy.py                  la funcion decide()

       │                                │
       ▼                                ▼
  ~6 parametros x                 Infinitas posibilidades:
  ~5 valores cada uno             filtros, logica, timing,
  = ~30 combinaciones             sizing, cross-coin...
```

---

## 7. La Metrica RAPR

### Definicion

```
RAPR = net_pnl_per_hour * consistency * fill_rate
```

Es un **unico numero** que captura tres dimensiones del rendimiento:

### Componente 1: Rentabilidad (`net_pnl_per_hour`)

```
net_pnl_per_hour = sum(pnl de todos los trades) / horas transcurridas

Ejemplo:
  - 10 trades en 30 min, PnL total = $0.50
  - net_pnl_per_hour = $0.50 / 0.5h = $1.00/hora
```

### Componente 2: Consistencia (`consistency`)

```
consistency = min(|mean_pnl| / std_pnl, 3.0)

Es un ratio Sharpe-like. Mide cuan "estable" son tus ganancias:

  Alto (>2): Ganas siempre cantidades similares (bueno)
  Bajo (<1): A veces ganas mucho, a veces pierdes (inestable)
  Cap a 3.0: Para evitar que outliers dominen

Ejemplo:
  PnLs = [0.05, 0.06, 0.04, 0.05, 0.05]
  mean = 0.05, std = 0.007
  consistency = min(0.05/0.007, 3.0) = min(7.1, 3.0) = 3.0 (cap)

  PnLs = [0.20, -0.10, 0.15, -0.05, 0.30]
  mean = 0.10, std = 0.16
  consistency = min(0.10/0.16, 3.0) = 0.625 (bajo)
```

### Componente 3: Tasa de Fill (`fill_rate`)

```
fill_rate = trades_filled / trades_totales

Ejemplo:
  - 10 trades intentados, 8 filled
  - fill_rate = 0.8

Importante: una estrategia que intenta 100 trades pero solo llena 10
es peor que una que intenta 15 y llena 12, incluso si el PnL es similar.
```

### Ejemplo Completo

```
Estrategia A:
  Trades: 20 en 30 min, 15 filled
  PnLs: [0.05, 0.08, 0.03, -0.02, 0.06, 0.04, 0.07, 0.03,
         0.05, 0.09, 0.04, -0.01, 0.06, 0.05, 0.02]
  Total PnL: $0.64
  net_pnl_per_hour = 0.64 / 0.5 = 1.28
  mean = 0.0427, std = 0.028
  consistency = min(0.0427/0.028, 3.0) = 1.52
  fill_rate = 15/20 = 0.75
  RAPR = 1.28 * 1.52 * 0.75 = 1.46

Estrategia B:
  Trades: 8 en 30 min, 7 filled
  PnLs: [0.15, 0.12, 0.18, 0.10, 0.14, 0.16, 0.13]
  Total PnL: $0.98
  net_pnl_per_hour = 0.98 / 0.5 = 1.96
  mean = 0.14, std = 0.027
  consistency = min(0.14/0.027, 3.0) = 3.0 (cap)
  fill_rate = 7/8 = 0.875
  RAPR = 1.96 * 3.0 * 0.875 = 5.15

Estrategia B es mejor: menos trades pero mas rentables y consistentes.
RAPR captura eso: 5.15 vs 1.46.
```

---

## 8. Rigor Estadistico - Welch's t-test

### El Problema

Los mercados son ruidosos. Si cambias un parametro y el PnL sube, podria ser por casualidad (el mercado estuvo mas favorable esos 30 minutos). Necesitamos distinguir **senal real** de **ruido aleatorio**.

### La Solucion: Welch's t-test

El test de Welch compara las medias de dos muestras (baseline vs test) considerando que pueden tener varianzas diferentes:

```
          mean_test - mean_baseline
t = ─────────────────────────────────────
     sqrt(var_test/n_test + var_baseline/n_baseline)

Grados de libertad (Welch-Satterthwaite):
         (var1/n1 + var2/n2)^2
df = ─────────────────────────────────────────
     (var1/n1)^2/(n1-1) + (var2/n2)^2/(n2-1)
```

**Implementacion**: Pure Python, sin scipy. Usa la aproximacion de Abramowitz & Stegun para la CDF normal (precisa a 6 decimales).

### Interpretacion del p-value

```
p-value = probabilidad de observar esta diferencia POR AZAR

  p < 0.01  ->  99% de confianza de que es real
  p < 0.05  ->  95% de confianza
  p < 0.10  ->  90% de confianza
  p > 0.10  ->  No hay evidencia suficiente
```

### Nuestros Criterios

```
┌─────────────────────────────────────────────────────────┐
│  Mejora RAPR > 5%  AND  p < 0.10                        │
│  ├── SI                                                  │
│  │   ├── Mejora > 30% AND p < 0.01 -> CONFIRM           │
│  │   │   (repetir test para verificar)                   │
│  │   └── Else -> KEEP                                    │
│  └── NO -> DISCARD                                       │
│                                                          │
│  EXCEPCION: Mejora > 0% AND p < 0.05 -> KEEP            │
│  (marginal pero estadisticamente significativa)          │
└─────────────────────────────────────────────────────────┘
```

### Por que p < 0.10 y no 0.05

Con muestras pequenas (~10-30 trades por fase), usar p < 0.05 seria demasiado estricto. Compensamos con:
1. La regla de confirmacion para resultados muy buenos (>30%)
2. El requisito de que RAPR mejore >5% (no solo significancia estadistica)
3. Cada trade tiene multiples dimensiones (PnL, fees, slippage)

### Datos Disponibles por Experimento

```
Baseline (30 min):
  - 60 polls x 5 monedas = 300 observaciones
  - ~5-30 trades (depende del mercado y estrategia)

Test (30 min):
  - 60 polls x 5 monedas = 300 observaciones
  - ~5-30 trades

Total: 600 observaciones, ~10-60 trades
```

---

## 9. El Paper Trader - Simulacion Realista

### Por que no un simple "compra al ask"

En trading real, el precio que ves NO es el precio que pagas. Hay multiples factores que erosionan tu edge:

```
┌───────────────────────────────────────────────────────┐
│                  DIFERENCIA REAL                       │
│                                                        │
│  Precio que VES:     YES ask = $0.48                   │
│                      NO ask  = $0.50                   │
│                      Total   = $0.98                   │
│                      Gap     = 2.0%                    │
│                                                        │
│  Precio que PAGAS:                                     │
│    Orderbook walk:   YES fill = $0.4834 (+0.7%)       │
│    Orderbook walk:   NO fill  = $0.5012 (+0.2%)       │
│    Slippage:         +0.2% (impacto de mercado)       │
│    Latencia:         +/-0.1% (movimiento en 300ms)    │
│                      ──────────────────                │
│    Total real:       $0.9893                           │
│    Fees:             $0.0115                           │
│    Gas:              $0.0100                           │
│                      ──────────────────                │
│    Outlay total:     $1.0108                           │
│                                                        │
│    Gap teórico: 2.0%                                   │
│    Gap REAL:    -1.1%   <-- PERDIDA                    │
│                                                        │
│  El paper trader simula TODO esto.                     │
│  Si tu estrategia es rentable en la simulacion,        │
│  tiene buenas probabilidades de serlo en real.         │
└───────────────────────────────────────────────────────┘
```

### Factores Simulados

| Factor | Como | Impacto |
|--------|------|---------|
| **Orderbook walk** | VWAP recorriendo niveles del libro | +0.1-2% sobre best ask |
| **Slippage** | `min((size/depth)^2 * 0.5, 2%)` | +0.1-2% |
| **Fill probability** | `spread_factor * depth_factor` | 30-98% chance de fill |
| **Latencia** | Random 0-0.2% movimiento | +/-0.2% |
| **Fees** | `p*(1-p)*0.022` por lado exacto | ~1.1% round-trip |
| **Gas** | $0.005 * 2 txs en Polygon | ~$0.01 fijo |

---

## 10. El Dashboard

### Acceso

```
URL: http://localhost:8080
Auto-refresh: cada 15 segundos
```

### 4 Tabs

**Tab 1: Experimentos** (por defecto)
- Tabla de historial: #, hipotesis, RAPR baseline, RAPR test, mejora%, p-value, estado
- Grafico de linea: evolucion del RAPR score
- Experimento activo: fase actual, hipotesis, progreso

**Tab 2: Trades**
- Tabla de trades recientes: moneda, size, cost, fees, PnL, filled, fase
- Grafico de barras: PnL por trade (verde=ganancia, rojo=perdida)
- Equity curve: evolucion del balance

**Tab 3: Rendimiento**
- PnL por moneda (barras)
- Histograma de gaps detectados
- Desglose de costes: profit bruto, fees totales, slippage, profit neto

**Tab 4: Research Log**
- Contenido de results.tsv
- Codigo actual de strategy.py

### KPIs (siempre visibles)

```
┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
│ Balance  │ PnL Total│ Win Rate │  Trades  │Experim.  │  Polls   │
│ $1,000   │ $0.00    │ 0% (0/0)│  0       │ 0 total  │ 0        │
│          │          │          │Fees: $0  │ 0 kept   │cada 30s  │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
```

### Coin Cards (5 tarjetas, siempre visibles)

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ ● BTC  [ARB] │  │ ○ ETH        │  │ ○ SOL        │
│ YES ask 0.48 │  │ YES ask 0.51 │  │ YES ask 0.52 │
│ NO ask  0.50 │  │ NO ask  0.50 │  │ NO ask  0.49 │
│ Total  0.980 │  │ Total  1.010 │  │ Total  1.010 │
│ Gap  +2.00%  │  │ Gap  -1.00%  │  │ Gap  -1.00%  │
│ Binance $67K │  │ Binance $2K  │  │ Binance $82  │
└──────────────┘  └──────────────┘  └──────────────┘
```

---

## 11. Como Ejecutarlo

### Requisitos

```bash
# Python 3.10+ (ya instalado)
# No necesita pip install de nada especial
# Todas las dependencias son standard library excepto:
pip install requests   # Para llamadas HTTP a las APIs
```

### Opcion 1: Modo Autonomo (sin IA)

El sistema corre solo, mutando parametros aleatoriamente.

```bash
# Terminal 1: Dashboard
cd C:\Proyectos_ignasi\autoresearch_polymarket
python server.py
# -> Dashboard en http://localhost:8080

# Terminal 2: Orchestrator
cd C:\Proyectos_ignasi\autoresearch_polymarket
python orchestrator.py
# -> Empieza el bucle infinito
# -> Ctrl+C para parar
```

**Que pasa cuando lo ejecutas**:

```
[HH:MM:SS] ============================================================
[HH:MM:SS]   AUTORESEARCH POLYMARKET - Binary Arbitrage
[HH:MM:SS]   Karpathy-style autonomous research loop
[HH:MM:SS] ============================================================
[HH:MM:SS] [INIT] Discovering 5-min markets...
[HH:MM:SS] [FETCH] Discovering 5-min markets for 5 coins...
[HH:MM:SS] [FETCH]   BTC: Bitcoin Up or Down - March 29, 5:55PM ET
[HH:MM:SS] [FETCH]   ETH: Ethereum Up or Down - March 29, 5:55PM ET
[HH:MM:SS] [FETCH]   SOL: Solana Up or Down - March 29, 5:55PM ET
[HH:MM:SS] [FETCH]   XRP: XRP Up or Down - March 29, 5:55PM ET
[HH:MM:SS] [FETCH]   DOGE: Dogecoin Up or Down - March 29, 6:00PM ET
[HH:MM:SS]   Markets ready: ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE']
[HH:MM:SS] [PHASE 0] Quick observation (5 min)...
[HH:MM:SS] ==================================================
[HH:MM:SS] PHASE: OBSERVE (5 min)
[HH:MM:SS] ==================================================
             ... (polls cada 30 seg) ...
[HH:MM:SS] PHASE OBSERVE COMPLETE
[HH:MM:SS]   Polls: 10 | Opportunities: 0
[HH:MM:SS] ============================================================
[HH:MM:SS]   STARTING AUTONOMOUS RESEARCH LOOP
[HH:MM:SS] ============================================================
[HH:MM:SS] ############################################################
[HH:MM:SS]   EXPERIMENT #1
[HH:MM:SS] ############################################################
[HH:MM:SS] [1/5] Running BASELINE (30 min)...
             ... (30 min de polls y trades) ...
[HH:MM:SS] [MUTATION] Change MIN_GAP_CENTS from 1.5 to 2.0
[HH:MM:SS] [3/5] Running TEST (30 min)...
             ... (30 min de polls y trades) ...
[HH:MM:SS] [4/5] Evaluating experiment #1...
[HH:MM:SS] === Experiment Evaluation ===
[HH:MM:SS] Result: IMPROVED
[HH:MM:SS] RAPR baseline=0.123456 test=0.234567
[HH:MM:SS] Improvement: +89.9%
[HH:MM:SS] p-value: 0.0423
[HH:MM:SS]   >>> Experiment #1: KEPT
[HH:MM:SS] [COOLDOWN] Waiting 5 min...
             ... (siguiente experimento) ...
```

### Opcion 2: Con Claude Code (modo Karpathy completo)

```bash
# Terminal 1: Dashboard
python server.py

# Terminal 2: Claude Code (en lugar de orchestrator.py)
# Abrir Claude Code en C:\Proyectos_ignasi\autoresearch_polymarket
# Darle la instruccion:

"Lee program.md y results.tsv. Eres un agente de investigacion autonomo
que optimiza una estrategia de binary arbitrage en Polymarket.

Tu trabajo:
1. Analiza el estado actual de strategy.py y los resultados previos
2. Formula una hipotesis sobre que cambio podria mejorar el RAPR
3. Modifica strategy.py segun tu hipotesis
4. Ejecuta: python -c "from orchestrator import *; ..."
   (o ejecuta las fases manualmente)
5. Analiza los resultados
6. Decide keep/discard
7. Propone el siguiente experimento

Sigue las reglas de program.md estrictamente:
- Un cambio a la vez
- Hipotesis antes de codigo
- RAPR como metrica principal
- p < 0.10 para mantener
- Nunca modifiques orchestrator.py, paper_trader.py, scorer.py, db.py"
```

### Opcion 3: Hibrida (recomendada para empezar)

```bash
# 1. Lanzar dashboard
python server.py

# 2. Lanzar orchestrator en modo autonomo unas horas
python orchestrator.py
# -> Deja que explore parametros basicos (~5-8 experimentos)
# -> Ctrl+C cuando tengas suficientes datos

# 3. Revisar results.tsv
# 4. Abrir Claude Code y darle contexto:
"Mira results.tsv: estos son los experimentos que el sistema autonomo
ha ejecutado. Ahora tu tomas el control. Analiza los resultados,
identifica patrones, y propone cambios mas inteligentes a strategy.py
(no solo parametros - puedes cambiar logica, anadir filtros, etc.)"
```

### Horario de Operacion

```
IMPORTANTE: Los mercados "Up or Down" de 5 minutos solo estan activos
durante el horario de trading de EEUU.

  Horario optimo: 9:30 AM - 6:00 PM Eastern Time (ET)
  En CET (hora espanola): 15:30 - 00:00

  Fuera de este horario:
  - Los mercados existen pero son para el dia siguiente
  - Los orderbooks estan casi vacios
  - El gap sera negativo o cero
  - El sistema polleara pero no encontrara oportunidades

Para maxima efectividad:
  - Arrancar el sistema a las 15:00 CET (antes de apertura US)
  - Dejarlo correr hasta las 00:00 CET
  - Eso da ~8.5 horas = ~7-8 experimentos
```

### Verificar que Funciona

```bash
# 1. Verificar DB
python db.py
# -> "[DB] Initialized: .../research.db"

# 2. Verificar APIs
python market_fetcher.py
# -> Deberia mostrar 5 monedas con precios

# 3. Verificar dashboard
python server.py
# -> Abrir http://localhost:8080
# -> Deberia cargar la pagina (puede estar vacia si no hay datos)

# 4. Test rapido del orchestrator (1 minuto)
python -c "
from orchestrator import run_phase, export_dashboard_data
import market_fetcher
from paper_trader import RealisticPaperTrader
market_fetcher._market_cache = {}
trader = RealisticPaperTrader()
trades = run_phase('quick_test', 1.0, trader)
export_dashboard_data()
print(f'OK: {len(trades)} trades')
"
```

---

## 12. Flujo Completo Paso a Paso

### Minuto a Minuto de un Experimento

```
TIEMPO    EVENTO                                  DETALLE
──────    ──────                                  ───────

T+0:00    BASELINE COMIENZA                       strategy.py con params actuales
T+0:00    Poll #1: 5 monedas                      BTC, ETH, SOL, XRP, DOGE
T+0:00    strategy.decide() -> 0 trades           No hay gaps suficientes
T+0:30    Poll #2: 5 monedas                      Prices actualizados
T+0:30    strategy.decide() -> 1 trade            BTC gap=2.1% > 1.5% threshold
T+0:30    paper_trader -> FILLED                  PnL=$0.09, fees=$0.011
T+1:00    Poll #3                                 ...
  ...     (continua cada 30 seg)                  ...
T+2:30    Poll #6 - status update                 "Trades: 3 | PnL: $0.27"
  ...
T+30:00   BASELINE COMPLETO                       60 polls, 12 trades, RAPR=0.82

T+30:01   MUTACION                                "Change MAX_SPREAD from 0.04 to 0.03"
T+30:01   strategy.py modificado                  MAX_SPREAD = 0.03
T+30:01   importlib.reload(strategy)              Hot-reload en memoria
T+30:01   git commit                              "exp-1: Change MAX_SPREAD 0.04->0.03"

T+30:01   TEST COMIENZA                           strategy.py con nuevo MAX_SPREAD
T+30:01   Poll #1 con nueva estrategia            ...
  ...     (30 min de polls)                       ...
T+60:00   TEST COMPLETO                           60 polls, 8 trades, RAPR=0.65

T+60:01   EVALUACION
          RAPR baseline: 0.82
          RAPR test: 0.65
          Mejora: -20.7%
          p-value: 0.32
          RESULTADO: DISCARD                      MAX_SPREAD=0.03 es peor

T+60:02   REVERT
          strategy.py <- strategy_default.py      Volver a MAX_SPREAD=0.04
          git reset HEAD~1 --hard                 Borrar commit del experimento

T+60:02   COOLDOWN (5 min)                        Descanso

T+65:00   EXPERIMENT #2 COMIENZA                  Nuevo parametro, nueva hipotesis
  ...
```

### Diagrama de Estados del Experimento

```
                    ┌──────────┐
                    │ PROPOSED │ -- Hipotesis creada, strategy.py modificado
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ BASELINE │ -- 30 min con estrategia ANTERIOR
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ RUNNING  │ -- 30 min con estrategia NUEVA (hot-reloaded)
                    └────┬─────┘
                         │
                 ┌───────┼───────┐
                 │       │       │
            ┌────▼──┐ ┌──▼───┐ ┌▼──────┐
            │COMPLET│ │REVERT│ │CRASHED│
            │  ED   │ │  ED  │ │       │
            │(KEEP) │ │(DISC)│ │(ERROR)│
            └───────┘ └──────┘ └───────┘
```

---

## 13. FAQ y Troubleshooting

### No encuentra mercados para las 5 monedas

Los mercados de 5 minutos solo se crean durante horario US (9:30 AM - 6:00 PM ET). Fuera de ese horario, puede que solo encuentre mercados para el dia siguiente con orderbooks vacios.

### Los gaps son siempre negativos

Normal fuera de horario. Los orderbooks estan vacios y los precios default son 0.99/0.99 (total 1.98). Durante horario activo, los gaps oscilan entre -2% y +3%.

### El sistema no hace trades

Posibles causas:
1. **Fuera de horario**: no hay gaps positivos
2. **MIN_GAP_CENTS muy alto**: bajar a 1.0 o 0.5
3. **MAX_SPREAD muy bajo**: subir a 0.05 o 0.08
4. **MIN_DEPTH_USD muy alto**: bajar a 25 o 10

### Como reiniciar desde cero

```bash
# Borrar la DB (perder todo el historial)
del data\research.db

# Reinicializar
python db.py

# Restaurar strategy.py por defecto
copy strategy_default.py strategy.py
```

### Como ver los datos crudos

```bash
# Abrir la DB con sqlite3
sqlite3 data/research.db

# Ver ultimos polls
SELECT coin, gap, total_ask, spread_yes FROM polls ORDER BY id DESC LIMIT 20;

# Ver trades
SELECT coin, net_pnl, fees, filled, phase FROM trades ORDER BY id DESC LIMIT 20;

# Ver experimentos
SELECT id, hypothesis, baseline_rapr, test_rapr, improvement_pct, status
FROM experiments ORDER BY id DESC;
```

### Cuanto dinero necesito para operar en real?

Este sistema es **solo paper trading**. Si quisieras operar en real:
- Minimo recomendado: $500 (para diversificar en 5 monedas)
- El edge es pequeno (~0.5-2% por trade de $10)
- Necesitas muchos trades para que sea rentable
- Los fees y slippage son reales y significativos
- **NO recomendamos operar en real sin meses de paper trading validado**

### Que pasa si strategy.py tiene un error de sintaxis?

El sistema esta protegido:
1. `importlib.reload(strategy)` esta dentro de un try/except
2. Si falla, automaticamente copia `strategy_default.py` sobre `strategy.py`
3. Hace otro reload con el default (que se sabe que funciona)
4. Marca el experimento como "crashed"
5. Continua con el siguiente experimento

---

## Apendice: Referencia Rapida

### Parametros del Orchestrator

| Parametro | Valor | Descripcion |
|-----------|-------|-------------|
| `POLL_INTERVAL_SECS` | 30 | Frecuencia de polling |
| `PHASE_DURATION_MINS` | 30 | Minutos por fase |
| `COOLDOWN_MINS` | 5 | Pausa entre experimentos |
| `OBSERVE_MINS` | 5 | Observacion inicial |
| `MIN_TRADES_TO_EVALUATE` | 3 | Min trades para evaluar |

### Parametros de la Estrategia Default

| Parametro | Valor | Rango sugerido |
|-----------|-------|---------------|
| `MIN_GAP_CENTS` | 1.5 | 0.5 - 5.0 |
| `MAX_SPREAD` | 0.04 | 0.02 - 0.10 |
| `MIN_DEPTH_USD` | 50 | 10 - 200 |
| `ORDER_SIZE_USD` | 10 | 5 - 50 |
| `MAX_TOTAL_COST` | 0.99 | 0.98 - 0.998 |
| `MAX_TRADES_PER_POLL` | 2 | 1 - 5 |

### Criterios de Decision

| Resultado | Condicion | Accion |
|-----------|-----------|--------|
| **KEEP** | RAPR +5%, p < 0.10 | Mantener cambio |
| **CONFIRM** | RAPR +30%, p < 0.01 | Repetir test |
| **DISCARD** | Cualquier otro caso | Revertir |
| **CRASH** | Error en strategy.py | Auto-revertir |

### APIs Usadas

| API | Endpoint | Auth | Rate Limit |
|-----|----------|------|-----------|
| Polymarket Gamma | `gamma-api.polymarket.com/markets` | No | ~60 req/min |
| Polymarket CLOB | `clob.polymarket.com/book` | No | ~120 req/min |
| Polymarket CLOB | `clob.polymarket.com/midpoint` | No | ~120 req/min |
| Binance | `api.binance.com/api/v3/ticker/price` | No | ~1200 req/min |
| Binance | `api.binance.com/api/v3/klines` | No | ~1200 req/min |

### Comandos Utiles

```bash
# Arrancar todo
python server.py &          # Dashboard en background
python orchestrator.py      # Loop principal

# Solo dashboard (ver datos existentes)
python server.py

# Test rapido de APIs
python market_fetcher.py

# Inicializar/reiniciar DB
python db.py

# Ver resultados
cat results.tsv
type results.tsv            # Windows

# Ver estado del portfolio
python -c "from paper_trader import RealisticPaperTrader; t=RealisticPaperTrader(); print(t.get_portfolio_summary())"
```
