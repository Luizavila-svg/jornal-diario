import os
from flask import Flask, render_template
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from jornal import get_resumos_hoje

load_dotenv()

app = Flask(__name__)

scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
scheduler.add_job(get_resumos_hoje, "cron", hour=6, minute=0)
scheduler.start()


@app.route("/")
@app.route("/jornal")
def jornal():
    try:
        resumos, destaques, gerado_em, data_formatada = get_resumos_hoje()
    except Exception as e:
        app.logger.exception(f"Erro ao gerar jornal: {e}")
        return "Erro ao gerar o jornal. Tente novamente em instantes.", 500
    return render_template(
        "jornal.html",
        resumos=resumos,
        destaques=destaques,
        gerado_em=gerado_em,
        data_formatada=data_formatada,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(debug=False, port=port)
