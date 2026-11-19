# FinWise AI 💸

### Your finances, understood. Your future, anticipated. Your decisions, empowered.

**FinWise AI** is an AI-powered personal financial decision-intelligence
platform that goes beyond traditional expense tracking. It transforms
transaction history into actionable insights using a **Financial Digital
Twin, statistical forecasting, machine-learning-based anomaly detection,
what-if simulations, financial stress testing, and an AI Financial Coach
powered by GPT-OSS 120B through Groq**.

> **The key idea:** Don't just track your money. Understand it, predict
> what may happen next, and evaluate financial decisions before making
> them.

------------------------------------------------------------------------

## ✨ Why FinWise AI?

Most expense trackers tell you **where your money went**. FinWise AI is
designed to help answer more forward-looking questions:

-   Can I afford a major purchase without putting my cash flow under
    pressure?
-   How might my balance change over the next 7, 30, or 90 days?
-   Is a transaction or spending pattern unusual compared with my
    history?
-   What could happen if my salary is delayed or my income stops?
-   How could reducing a spending category affect my savings goal?
-   What is driving my financial position, and what actions could
    improve it?

FinWise combines deterministic financial computation with an LLM-based
conversational interface. The AI can select financial tools, retrieve
computed results, and explain them in natural language.

------------------------------------------------------------------------

## 🤖 AI at the Core

### AI Financial Coach --- GPT-OSS 120B + Groq

The AI Coach is more than a general-purpose chatbot. It is connected to
FinWise's financial services through **LLM tool calling (function
calling)**.

A user can ask questions in natural language, and the model can choose
from server-defined tools to retrieve financial information or run
supported calculations.

**Examples of questions:**

-   "Can I afford to buy a laptop for ₹80,000?"
-   "Why did my expenses increase this month?"
-   "How much could I save if I reduce food spending by 15%?"
-   "What happens if my salary is delayed by 20 days?"
-   "Am I on track to reach my savings goal?"
-   "Show me the transactions that look unusual."

**How the AI workflow operates:**

1.  **Understand:** The LLM interprets the user's question and
    identifies the financial task.
2.  **Select a tool:** It chooses an appropriate server-side financial
    function.
3.  **Compute:** FinWise's Python services retrieve data and perform the
    calculation.
4.  **Return results:** Structured tool results are provided to the
    model.
5.  **Explain:** The LLM turns those results into a contextual,
    conversational response.

### A deliberate design choice: AI explains; Python calculates

Financial figures are not left to the language model to invent.
Forecasts, balances, spending totals, and simulation results are
calculated by FinWise's deterministic Python services. The LLM selects
tools and explains their returned results.

This separation makes the AI experience more grounded, auditable, and
appropriate for a financial decision-support application.

### AI tool capabilities

The AI Coach has access to server-defined financial tools, including
capabilities to:

  -----------------------------------------------------------------------
  Tool capability                     What it enables
  ----------------------------------- -----------------------------------
  Financial summary                   Retrieve a consolidated view of the
                                      user's finances

  Spending by category                Explain where money is being spent

  Transaction history                 Retrieve relevant transaction
                                      records

  Recurring payments                  Review regular financial
                                      commitments

  Future balance prediction           Retrieve calculated balance
                                      forecasts

  Purchase simulation                 Evaluate the impact of a potential
                                      purchase

  Financial stress testing            Explore hypothetical adverse
                                      scenarios

  Financial goals                     Review savings goals and progress

  Budget status                       Check budget utilisation
  -----------------------------------------------------------------------

The tool layer is designed to keep financial data access and
calculations in the backend rather than allowing the model to directly
manipulate the database.

------------------------------------------------------------------------

## 🧠 Core Features

### 1. Financial Digital Twin

A virtual representation of a user's financial position, built from
their recorded financial activity.

It brings together information such as:

-   Income and expenses
-   Current and projected balances
-   Recurring payments and commitments
-   Savings rate and cash-flow trends
-   Financial goals and budgets
-   Detected anomalies and financial risks

The Digital Twin connects historical financial activity with analytics
and forecasts to provide a consolidated view of the user's financial
condition.

### 2. Predictive Financial Forecasting

FinWise uses statistical forecasting methods to estimate future
financial activity and balances.

**Monthly forecasting methods include:**

  Available history   Forecasting approach
  ------------------- -----------------------------------
  1--2 months         Historical average
  3--5 months         Weighted moving average
  6 or more months    Holt damped exponential smoothing

Daily balance forecasts support 7-day, 30-day, and 90-day horizons, with
prediction ranges where available. Forecasts take relevant income,
recurring payments, and spending patterns into account.

### 3. Machine Learning-Based Anomaly Detection

The anomaly engine identifies transactions and financial patterns that
may deserve attention.

Techniques and checks include:

-   Robust Z-score analysis
-   Isolation Forest for unusual transaction patterns
-   Duplicate transaction detection
-   Unusually large withdrawals
-   Sudden spending surges
-   New spending categories
-   Changes in recurring payments
-   Unusual cash-flow changes

An anomaly is an unusual pattern, **not proof of fraud**. Alerts are
intended to help users review activity.

### 4. What-If Financial Simulator

Explore hypothetical financial decisions before committing to them.

Supported scenario types include:

-   Major purchases
-   EMI purchases
-   Loans
-   Savings changes
-   Spending reductions by category
-   Salary delays or income loss
-   Unexpected expenses
-   Income increases
-   New recurring subscriptions

The simulator evaluates the potential impact on projected balances and
financial goals.

### 5. Financial Stress Testing

Explore how a financial position could respond to adverse events, such
as delayed salary, income loss, or unexpected expenses. This helps users
examine potential vulnerabilities and understand the importance of
financial buffers.

### 6. Transactions, Budgets and Goals

-   Add, edit, filter, and manage transactions
-   Import financial statements
-   Categorize transactions using rules and machine-learning support
-   Detect recurring income and payments
-   Track budgets and spending limits
-   Create and monitor savings goals
-   Review notifications and financial alerts
-   Export personal financial data

### 7. Financial Data Import

The application supports importing financial records from CSV, Excel,
and PDF files. Parsing and validation take place in the backend, with
optional OCR configuration for scanned PDF workflows.

------------------------------------------------------------------------

## 🏗️ System Architecture

FinWise AI uses a modular-monolith backend architecture. The frontend
communicates with versioned REST APIs, while financial business logic is
separated into Python services.

``` text
                    USER
                     |
                     v
       FRONTEND — HTML / CSS / JavaScript
                     |
              REST API / JSON
                     |
                     v
             FASTAPI BACKEND
                     |
       +-------------+--------------+
       |             |              |
       v             v              v
  Financial      AI Coach       Data & User
  Services       Service        Management
       |             |              |
       |        Groq API             |
       |       GPT-OSS 120B          |
       |             |              |
       |<------ Tool Calling ------->|
       |
       +-------------+--------------+
                     |
                     v
             SQLAlchemy ORM
                     |
                     v
          PostgreSQL / SQLite
```

### Technology Stack

  Layer                     Technologies
  ------------------------- ---------------------------------
  Frontend                  HTML5, CSS3, Vanilla JavaScript
  Backend                   Python, FastAPI
  API                       REST, JSON
  Database                  PostgreSQL, SQLite
  ORM and migrations        SQLAlchemy, Alembic
  Data processing           Pandas, NumPy
  Machine learning          Scikit-learn
  Statistical forecasting   Statsmodels
  Generative AI             GPT-OSS 120B through Groq API
  Authentication            Supabase Auth, JWT validation
  File processing           PyMuPDF, OpenPyXL
  Testing                   Pytest

------------------------------------------------------------------------

## 📂 Project Structure

``` text
finwise/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── v1/             # Versioned API routes
│   │   ├── core/               # Configuration, database, security
│   │   ├── ml/                 # Machine-learning components
│   │   ├── models/             # SQLAlchemy models
│   │   ├── schemas/            # Request and response schemas
│   │   ├── services/
│   │   │   ├── ai_coach.py
│   │   │   ├── analytics.py
│   │   │   ├── anomalies.py
│   │   │   ├── categorization.py
│   │   │   ├── forecasting.py
│   │   │   ├── simulation.py
│   │   │   ├── twin.py
│   │   │   └── ...
│   │   └── main.py
│   ├── alembic/                # Database migrations
│   ├── docs/                   # API and database documentation
│   ├── scripts/                # Seed and utility scripts
│   ├── tests/
│   ├── requirements.txt
│   └── README.md
│
└── frontend/
    ├── assets/
    ├── css/
    ├── js/
    ├── sample-data/
    ├── index.html
    └── README.md
```

------------------------------------------------------------------------

## 🚀 Getting Started

### Prerequisites

-   Python 3.11 or later
-   Git
-   PostgreSQL, or SQLite for a local trial
-   A Supabase project for authentication
-   A Groq API key to enable the AI Coach

### 1. Clone the repository

``` bash
git clone https://github.com/<your-username>/<your-repository>.git
cd <your-repository>
```

### 2. Set up the backend

``` bash
cd backend

python -m venv .venv
```

Activate the environment:

**Windows**

``` bash
.venv\Scripts\activate
```

**macOS / Linux**

``` bash
source .venv/bin/activate
```

Install dependencies:

``` bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the backend directory. Configure the following
variables as needed:

``` env
DATABASE_URL=sqlite:///./finwise_dev.db

SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_JWT_SECRET=

GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-120b
GROQ_REASONING_EFFORT=medium

CORS_ORIGINS=http://localhost:3000
ALLOW_DEV_AUTH=false
```

For PostgreSQL, replace `DATABASE_URL` with your PostgreSQL connection
URI.

**Important:** Keep API keys, database credentials, JWT secrets, and
other private configuration out of source control. Never commit your
`.env` file.

### 4. Run database migrations

``` bash
alembic upgrade head
```

### 5. Start the backend

``` bash
uvicorn app.main:app --reload
```

The API will be available at:

-   API: `http://localhost:8000`
-   Swagger documentation: `http://localhost:8000/docs`
-   ReDoc: `http://localhost:8000/redoc`

### 6. Start the frontend

Open a second terminal:

``` bash
cd frontend
python -m http.server 3000
```

Visit:

``` text
http://localhost:3000
```

Check `frontend/js/config.js` and configure the API base URL if your
backend is running at a different address. Ensure the frontend origin is
included in the backend's `CORS_ORIGINS`.

### 7. Enable the AI Coach

Add a valid Groq API key to the backend `.env` file:

``` env
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-120b
```

Restart the backend. The AI Coach requires valid provider credentials;
the rest of the application can run without the Groq integration.

For complete configuration details, see `backend/README.md`.

------------------------------------------------------------------------

## 🧪 Testing

The backend includes a Pytest suite covering core functionality,
financial engines, and security-related behaviour.

Run the tests from the backend directory:

``` bash
pytest -q
```

The test suite is designed to run without a live Groq API key; LLM
interactions are mocked in tests.

For PostgreSQL testing, configure `TEST_DATABASE_URL` as documented in
the backend README.

------------------------------------------------------------------------

## 🔐 Security and Privacy

FinWise is designed with security-conscious backend practices,
including:

-   Supabase JWT-based authentication
-   User-scoped data access in API queries
-   Input and file validation
-   Configurable CORS origins
-   Request rate limiting
-   Redacted application logs
-   Server-side handling of AI provider credentials
-   Financial data export and account deletion functionality

**Before production deployment:** review database permissions and
Supabase Row Level Security (RLS), use secure deployment secrets,
configure production CORS, and assess rate limiting and session
management for your deployment architecture.

Never upload or enter bank passwords, UPI PINs, OTPs, card CVVs, or
other payment authentication secrets.

------------------------------------------------------------------------

## 🗺️ Future Scope

Potential areas for further development include:

-   Bank API integrations and automated transaction synchronization
-   More robust statement parsing across financial institutions
-   Explainable forecasts showing the factors behind predictions
-   Prediction-versus-actual accuracy tracking
-   More extensive model evaluation and anomaly-detection tuning
-   Multi-currency support
-   Background processing for large imports and analytical workloads
-   Improved session renewal and production-grade infrastructure
-   More personalized, goal-aware financial recommendations

------------------------------------------------------------------------

## ⚠️ Disclaimer

FinWise AI is an informational financial analytics and decision-support
project. It is not a bank, investment platform, or regulated financial
advisor. Forecasts, anomaly alerts, simulations, and stress tests are
estimates or hypothetical scenarios based on available user data and
assumptions. They do not guarantee future results and should not be
treated as professional financial, investment, tax, or legal advice.

------------------------------------------------------------------------

## 👨‍💻 Author

**Your Name**

Add your GitHub profile, LinkedIn profile, and contact information here.

------------------------------------------------------------------------

```{=html}
<p align="center">
```
`<strong>`{=html}FinWise AI --- Understand your finances. Anticipate
tomorrow. Decide with confidence.`</strong>`{=html}
```{=html}
</p>
```
