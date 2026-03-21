# Sistema Multi-Agente de Trading Forex 🤖📈
**EUR/USD Algorithmic Trading con LLMs Locales (Ollama) — REAL MONEY READY**

> [!WARNING]
> ⚠️ **ADVERTENCIA / WARNING**
> Este sistema puede perder dinero real. Úsalo bajo tu propia responsabilidad.
> This system can lose real money. Use it at your own risk.

---

## 🏗 Arquitectura del Sistema

```text
┌─────────────────────────────────────────────────────────────┐
│                    ORQUESTADOR (llama3.1:8b)                │
│              Chief Trader — Tomador de Decisiones           │
└──────────┬──────────┬──────────┬──────────┬────────────────┘
           │          │          │          │
     ┌─────┴──┐ ┌─────┴──┐ ┌────┴───┐ ┌───┴────────┐
     │Fundmntl│ │Técnico │ │ Riesgo & │ │ Crítico    │
     │mistral │ │Python  │ │ Banquero │ │llama3.1:8b │
     │  7b    │ │  Puro  │ │ phi3:med │ │(Memoria DB)│
     └────────┘ └────────┘ └────────┘ └────────────┘
           │          │          │
     ┌─────┴──────────┴──────────┴──────────────────┐
     │                CAPA DE DATOS                 │
     │  Yahoo Finance (primario) + Simulador (resp) │
     └──────────────────────┬───────────────────────┘
                            │
     ┌──────────────────────┴───────────────────────┐
     │               CAPA DE EJECUCIÓN              │
     │   OANDA REST v20 | MT5 Bridge | Broker Paper │
     └──────────────────────────────────────────────┘
```

## 📋 Inicio Rápido

### Requisitos Previos
1. **Python 3.11+** instalado.
2. **Ollama** instalado (https://ollama.ai) con los siguientes modelos listos:
   ```bash
   ollama pull llama3.1:8b
   ollama pull mistral:7b
   ollama pull phi3:medium
   ```
3. **Clonar e instalar dependencias:**
   ```bash
   cd SistemaMultiagenteTrader
   pip install -r requirements.txt
   ```
4. **Configuración Inicial:**
   ```bash
   copy .env.example .env
   # Edita .env con tus credenciales y configuración
   ```

### Ejecutar (Paper Trading — Modo Seguro por defecto)
```bash
python main.py                          # Paper trading, ciclos de 15-min
python main.py --interval 5             # Ciclos de 5-minutos
python main.py --dry-run                # Un solo ciclo de análisis, sin órdenes
python main.py --no-dashboard           # Modo sin interfaz (headless)
```

### Ejecutar (Live Trading — DINERO REAL)
```bash
python main.py --live                   # Requiere confirmación manual doble
```

---

## 🧠 Los 5 Agentes

| Agente | Modelo | Función Principal |
|--------|--------|-------------------|
| **Orquestador** | `llama3.1:8b` | Chief Trader — Fusión de señales y decisión final |
| **Fundamental** | `mistral:7b` | Sentimiento de noticias + Calendario Económico |
| **Técnico** | Python Puro | Señales de EMA/RSI/MACD/ATR (Sin sesgo LLM) |
| **Riesgo/Banco** | `phi3:medium` | Kill-switch, cálculo de lotes, transferencias |
| **Crítico** | `llama3.1:8b` | Aprendizaje de trades pasados (Memoria SQLite) |

## 🛡 Sistema de Control de Riesgos

| Control | Disparador | Acción Automática |
|---------|-------------|-------------------|
| **Drawdown Diario** | > 2% | Kill Switch Inmediato |
| **Pérdidas Consec.** | 3 seguidas | Kill Switch Inmediato |
| **Evento Alto Impacto**| NFP/CPI/FOMC/ECB | NO TRADE (Ventana 30-min) |
| **Baja Confianza** | < 65% | Rebaja automática a HOLD |
| **Fallo del Broker** | Error de ejecución | Registro + Modo SEGURO |

## 📊 Salida de Decisión (JSON Estricto)
```json
{
  "decision": "BUY | SELL | HOLD",
  "confidence": 0.85,
  "risk_level": "LOW",
  "reasoning": "Alineación técnica y fundamental clara para entrada en largo.",
  "execution_priority": "IMMEDIATE",
  "invalidate_conditions": ["RSI sobrecomprado (>70)"]
}
```

## ⚙️ Parámetros Clave (`.env`)

```bash
TRADING_MODE=PAPER          # PAPER | LIVE
PAPER_CAPITAL=10000.0       # Balance de simulación
RISK_PER_TRADE_PCT=1.0      # % de cuenta arriesgado por trade
MAX_DAILY_DRAWDOWN_PCT=2.0  # Límite Max DD diario
OANDA_API_KEY=...           # Clave OANDA (Broker Real)
OANDA_ACCOUNT_ID=...
OLLAMA_BASE_URL=http://localhost:11434
```

## 🔒 Seguridad
- Todas las APIs e IDs residen en `.env` (fuera del repositorio).
- No hardcoding en el código fuente.
- Base de datos SQLite local para inmutabilidad y auditoría completa de decisiones y logs.
- Kill-switch con requerimiento de rearme manual.

---

## 💰 Apoya mi trabajo de código abierto

Si este sistema te resulta útil para tu propia operativa, desarrollo o investigación, considera invitarme un café. ¡Vuestro apoyo me ayuda a dedicar más tiempo al desarrollo de código abierto! 🙏

### Bitcoin

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  ₿  Bitcoin Donation Address  ₿   ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃                                   ┃
┃   bc1qqphwht25vjzlptwzjyjt3sex    ┃
┃   7e3p8twn390fkw                  ┃
┃                                   ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

**Red:** Bitcoin (BTC)
**Dirección:** `bc1qqphwht25vjzlptwzjyjt3sex7e3p8twn390fkw`

*Escanee el código QR en su wallet preferida o copie la dirección arriba indicada.*

---
*Desarrollado con ❤️ integrando Ollama, Yahoo Finance, OANDA, Rich y SQLAlchemy.*
