from __future__ import annotations

# NOTE: Flask-SQLAlchemy model constructors are dynamic (__init__(**kwargs));
# static analyzers can raise false constructor-argument errors for valid usage.
# pyright: reportCallIssue=false

import csv
import io
import os
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from statistics import mean

import click
from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from sqlalchemy import case, func, or_
from sqlalchemy.schema import CheckConstraint, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DEBUG_MODE = env_flag("FLASK_DEBUG", default=False)
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sqlite_path = os.environ.get("SQLITE_PATH", "proofloop.db")
    DATABASE_URL = f"sqlite:///{sqlite_path}"

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY and DEBUG_MODE:
    SECRET_KEY = "dev-only-secret-key"
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set when FLASK_DEBUG is off.")

app.config["SECRET_KEY"] = SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["DEBUG"] = DEBUG_MODE

db = SQLAlchemy(app)

FOCUS_AREAS = ["Energy", "Sleep", "Focus", "Stress"]
ENERGY_EXPERIMENTS = [
    "Morning walk",
    "No caffeine after 2pm",
    "10 minutes morning sunlight",
    "Drink water before first coffee",
    "No phone first 30 minutes",
    "Bed before fixed time",
    "Protein breakfast",
    "5-minute afternoon reset",
]


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Experiment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    focus_area = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    duration_days = db.Column(db.Integer, default=7, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class UserExperiment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    experiment_id = db.Column(db.Integer, db.ForeignKey("experiment.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    completed = db.Column(db.Boolean, default=False, nullable=False)

    user = db.relationship("User", backref="user_experiments")
    experiment = db.relationship("Experiment")


class CheckIn(db.Model):
    __table_args__ = (
        UniqueConstraint("user_experiment_id", "checkin_date", name="uq_checkin_per_day"),
        CheckConstraint("score >= 1 AND score <= 10", name="ck_checkin_score_range"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_experiment_id = db.Column(
        db.Integer, db.ForeignKey("user_experiment.id"), nullable=False
    )
    checkin_date = db.Column(db.Date, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    followed_experiment = db.Column(db.Boolean, nullable=False)
    note = db.Column(db.Text, nullable=True)

    user_experiment = db.relationship("UserExperiment", backref="checkins")


@app.before_request
def load_user():
    user_id = session.get("user_id")
    if user_id:
        user = User.query.get(user_id)
        if user is None:
            session.clear()


@app.context_processor
def inject_auth_state():
    user = current_user()
    return {
        "logged_in": user is not None,
        "current_user_email": user.email if user else None,
        "is_admin": is_admin_user(user),
    }


def current_user() -> User | None:
    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return User.query.get(user_id)


def login_required():
    if not current_user():
        flash("Please log in first.", "error")
        return False
    return True


def is_admin_user(user: User | None) -> bool:
    if not user:
        return False

    configured_admins = os.environ.get("ADMIN_EMAILS", "").strip()
    if not configured_admins:
        return False

    admin_set = {email.strip().lower() for email in configured_admins.split(",") if email}
    return user.email.lower() in admin_set


def admin_required() -> User | None:
    user = current_user()
    if not user:
        flash("Please log in first.", "error")
        return None
    if not os.environ.get("ADMIN_EMAILS", "").strip():
        flash("Admin access disabled: ADMIN_EMAILS is not configured.", "error")
        return None
    if not is_admin_user(user):
        flash("Admin access required.", "error")
        return None
    return user


def active_user_experiment(user_id: int) -> UserExperiment | None:
    return (
        UserExperiment.query.filter_by(user_id=user_id, completed=False)
        .order_by(UserExperiment.start_date.desc())
        .first()
    )


def results_summary(user_experiment: UserExperiment) -> dict:
    checkins = (
        CheckIn.query.filter_by(user_experiment_id=user_experiment.id)
        .order_by(CheckIn.checkin_date.asc())
        .all()
    )
    followed_scores = [c.score for c in checkins if c.followed_experiment]
    not_followed_scores = [c.score for c in checkins if not c.followed_experiment]

    avg_followed = mean(followed_scores) if followed_scores else None
    avg_not_followed = mean(not_followed_scores) if not_followed_scores else None

    impact_percent = None
    if avg_followed is not None and avg_not_followed and avg_not_followed != 0:
        impact_percent = ((avg_followed - avg_not_followed) / avg_not_followed) * 100

    confidence = "low"
    total = len(checkins)
    min_group = min(len(followed_scores), len(not_followed_scores)) if checkins else 0
    if min_group >= 3 and total >= 7:
        confidence = "high"
    elif min_group >= 2 and total >= 4:
        confidence = "medium"

    return {
        "total": total,
        "followed_count": len(followed_scores),
        "not_followed_count": len(not_followed_scores),
        "avg_followed": avg_followed,
        "avg_not_followed": avg_not_followed,
        "impact_percent": impact_percent,
        "confidence": confidence,
    }


def seed_experiments() -> None:
    existing = Experiment.query.count()
    if existing > 0:
        return

    for name in ENERGY_EXPERIMENTS:
        db.session.add(
            Experiment(
                focus_area="Energy",
                name=name,
                duration_days=7,
                is_active=True,
            )
        )
    db.session.commit()


def completion_subquery():
    return (
        db.session.query(
            UserExperiment.id.label("ue_id"),
            UserExperiment.user_id.label("user_id"),
            UserExperiment.completed.label("completed"),
            func.count(CheckIn.id).label("checkin_count"),
        )
        .outerjoin(CheckIn, CheckIn.user_experiment_id == UserExperiment.id)
        .group_by(UserExperiment.id)
        .subquery()
    )


def calculate_admin_metrics() -> dict:
    today = date.today()

    total_users = User.query.count()
    experiments_started = UserExperiment.query.count()

    active_users_today = (
        db.session.query(func.count(func.distinct(UserExperiment.user_id)))
        .join(CheckIn, CheckIn.user_experiment_id == UserExperiment.id)
        .filter(CheckIn.checkin_date == today)
        .scalar()
        or 0
    )

    users_with_1plus_checkin = (
        db.session.query(func.count(func.distinct(UserExperiment.user_id)))
        .join(CheckIn, CheckIn.user_experiment_id == UserExperiment.id)
        .scalar()
        or 0
    )

    user_checkin_counts = (
        db.session.query(
            UserExperiment.user_id.label("user_id"),
            func.count(CheckIn.id).label("checkin_count"),
        )
        .join(CheckIn, CheckIn.user_experiment_id == UserExperiment.id)
        .group_by(UserExperiment.user_id)
        .subquery()
    )

    users_with_5plus_checkins = (
        db.session.query(func.count())
        .select_from(user_checkin_counts)
        .filter(user_checkin_counts.c.checkin_count >= 5)
        .scalar()
        or 0
    )

    completion_sq = completion_subquery()

    users_completed_7_days = (
        db.session.query(func.count(func.distinct(completion_sq.c.user_id)))
        .filter(or_(completion_sq.c.completed.is_(True), completion_sq.c.checkin_count >= 7))
        .scalar()
        or 0
    )

    experiments_completed = (
        db.session.query(func.count())
        .select_from(completion_sq)
        .filter(or_(completion_sq.c.completed.is_(True), completion_sq.c.checkin_count >= 7))
        .scalar()
        or 0
    )

    completed_ue_ids = [
        row.ue_id
        for row in db.session.query(completion_sq.c.ue_id)
        .filter(or_(completion_sq.c.completed.is_(True), completion_sq.c.checkin_count >= 7))
        .all()
    ]

    avg_score_improvement = None
    if completed_ue_ids:
        rows = (
            db.session.query(
                CheckIn.user_experiment_id,
                func.avg(
                    case((CheckIn.followed_experiment.is_(True), CheckIn.score), else_=None)
                ).label("avg_followed"),
                func.avg(
                    case((CheckIn.followed_experiment.is_(False), CheckIn.score), else_=None)
                ).label("avg_not_followed"),
            )
            .filter(CheckIn.user_experiment_id.in_(completed_ue_ids))
            .group_by(CheckIn.user_experiment_id)
            .all()
        )

        impacts = []
        for row in rows:
            if row.avg_followed is None or row.avg_not_followed in (None, 0):
                continue
            impacts.append(((row.avg_followed - row.avg_not_followed) / row.avg_not_followed) * 100)

        if impacts:
            avg_score_improvement = mean(impacts)

    user_checkin_dates: dict[int, set[date]] = {}
    for row in (
        db.session.query(UserExperiment.user_id, CheckIn.checkin_date)
        .join(CheckIn, CheckIn.user_experiment_id == UserExperiment.id)
        .all()
    ):
        user_checkin_dates.setdefault(row.user_id, set()).add(row.checkin_date)

    all_users = User.query.all()

    def retention(day_offset: int) -> dict:
        eligible = 0
        retained = 0
        for user in all_users:
            created_day = user.created_at.date()
            age = (today - created_day).days
            if age < day_offset:
                continue
            eligible += 1
            expected_checkin_day = created_day + timedelta(days=day_offset)
            if expected_checkin_day in user_checkin_dates.get(user.id, set()):
                retained += 1

        pct = (retained / eligible * 100) if eligible else None
        return {"eligible": eligible, "retained": retained, "pct": pct}

    return {
        "total_users": total_users,
        "active_users_today": active_users_today,
        "users_with_1plus_checkin": users_with_1plus_checkin,
        "users_with_5plus_checkins": users_with_5plus_checkins,
        "users_completed_7_days": users_completed_7_days,
        "experiments_started": experiments_started,
        "experiments_completed": experiments_completed,
        "avg_score_improvement": avg_score_improvement,
        "retention_day_2": retention(2),
        "retention_day_7": retention(7),
        "retention_day_14": retention(14),
        "generated_at": datetime.utcnow(),
    }


def send_daily_reminder_emails(dry_run: bool = False) -> dict:
    today = date.today()
    pending_rows = (
        db.session.query(User.email, UserExperiment.id)
        .join(UserExperiment, UserExperiment.user_id == User.id)
        .filter(UserExperiment.completed.is_(False))
        .filter(UserExperiment.start_date <= today)
        .filter(UserExperiment.end_date >= today)
        .filter(
            ~db.session.query(CheckIn.id)
            .filter(CheckIn.user_experiment_id == UserExperiment.id)
            .filter(CheckIn.checkin_date == today)
            .exists()
        )
        .all()
    )

    recipients = sorted({row.email for row in pending_rows})
    if dry_run:
        return {"eligible": len(recipients), "sent": 0, "failed": 0, "dry_run": True}

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM_EMAIL", smtp_username)
    smtp_use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}
    app_base_url = os.environ.get("APP_BASE_URL", "http://127.0.0.1:5000")

    if not smtp_host or not smtp_from:
        raise RuntimeError("Missing SMTP configuration. Set SMTP_HOST and SMTP_FROM_EMAIL.")

    sent = 0
    failed = 0
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_username:
            server.login(smtp_username, smtp_password)

        for recipient in recipients:
            message = EmailMessage()
            message["Subject"] = "ProofLoop reminder: log today's check-in"
            message["From"] = smtp_from
            message["To"] = recipient
            message.set_content(
                "Quick reminder from ProofLoop. "
                "Log today's check-in (1-10 score + followed yes/no): "
                f"{app_base_url}/checkin"
            )
            try:
                server.send_message(message)
                sent += 1
            except Exception:
                failed += 1

    return {"eligible": len(recipients), "sent": sent, "failed": failed, "dry_run": False}


@app.route("/")
def landing():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("register.html")

        if User.query.filter_by(email=email).first():
            flash("That email is already registered.", "error")
            return render_template("register.html")

        user = User(email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()

        session.clear()
        session["user_id"] = user.id
        return redirect(url_for("choose_goal"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "error")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user.id
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/goal", methods=["GET", "POST"])
def choose_goal():
    if not login_required():
        return redirect(url_for("login"))

    if request.method == "POST":
        focus_area = request.form.get("focus_area")
        if focus_area not in FOCUS_AREAS:
            flash("Please choose a valid focus area.", "error")
            return render_template("goal.html", focus_areas=FOCUS_AREAS)

        session["focus_area"] = focus_area
        return redirect(url_for("choose_experiment"))

    return render_template("goal.html", focus_areas=FOCUS_AREAS)


@app.route("/experiment", methods=["GET", "POST"])
def choose_experiment():
    if not login_required():
        return redirect(url_for("login"))

    user = current_user()
    if not user:
        return redirect(url_for("login"))

    existing_active = active_user_experiment(user.id)
    if existing_active:
        flash("You already have an active experiment.", "info")
        return redirect(url_for("dashboard"))

    selected_focus = session.get("focus_area", "Energy")
    experiments = Experiment.query.filter_by(
        focus_area=selected_focus, is_active=True
    ).order_by(Experiment.name.asc()).all()

    if request.method == "POST":
        experiment_id = request.form.get("experiment_id", type=int)
        experiment = Experiment.query.filter_by(id=experiment_id, is_active=True).first()

        if not experiment:
            flash("Please choose a valid experiment.", "error")
            return render_template(
                "experiment.html", experiments=experiments, selected_focus=selected_focus
            )

        start = date.today()
        end = start + timedelta(days=experiment.duration_days - 1)

        user_experiment = UserExperiment(
            user_id=user.id,
            experiment_id=experiment.id,
            start_date=start,
            end_date=end,
            completed=False,
        )
        db.session.add(user_experiment)
        db.session.commit()

        flash("Great. Your 7-day experiment starts now.", "success")
        return redirect(url_for("checkin"))

    return render_template(
        "experiment.html", experiments=experiments, selected_focus=selected_focus
    )


@app.route("/dashboard")
def dashboard():
    if not login_required():
        return redirect(url_for("login"))

    user = current_user()
    if not user:
        return redirect(url_for("login"))

    current = active_user_experiment(user.id)
    summary = results_summary(current) if current else None

    return render_template("dashboard.html", current=current, summary=summary)


@app.route("/checkin", methods=["GET", "POST"])
def checkin():
    if not login_required():
        return redirect(url_for("login"))

    user = current_user()
    if not user:
        return redirect(url_for("login"))

    current = active_user_experiment(user.id)
    if not current:
        flash("Start an experiment first.", "error")
        return redirect(url_for("choose_goal"))

    today = date.today()

    if request.method == "POST":
        score = request.form.get("score", type=int)
        followed_raw = request.form.get("followed", "")
        note = request.form.get("note", "").strip()

        if score is None or score < 1 or score > 10:
            flash("Score must be between 1 and 10.", "error")
            return render_template("checkin.html", current=current)

        if followed_raw not in {"yes", "no"}:
            flash("Please answer whether you followed the experiment.", "error")
            return render_template("checkin.html", current=current)

        followed = followed_raw == "yes"

        existing = CheckIn.query.filter_by(
            user_experiment_id=current.id, checkin_date=today
        ).first()

        if existing:
            existing.score = score
            existing.followed_experiment = followed
            existing.note = note
            flash("Today\'s check-in updated.", "success")
        else:
            db.session.add(
                CheckIn(
                    user_experiment_id=current.id,
                    checkin_date=today,
                    score=score,
                    followed_experiment=followed,
                    note=note,
                )
            )
            flash("Check-in saved.", "success")

        if today >= current.end_date:
            current.completed = True

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Duplicate check-in blocked for today.", "error")
            return render_template("checkin.html", current=current)

        return redirect(url_for("results"))

    return render_template("checkin.html", current=current)


@app.route("/results")
def results():
    if not login_required():
        return redirect(url_for("login"))

    user = current_user()
    if not user:
        return redirect(url_for("login"))

    latest = (
        UserExperiment.query.filter_by(user_id=user.id)
        .order_by(UserExperiment.start_date.desc())
        .first()
    )

    if not latest:
        flash("Start an experiment to see results.", "info")
        return redirect(url_for("choose_goal"))

    summary = results_summary(latest)
    checkins = (
        CheckIn.query.filter_by(user_experiment_id=latest.id)
        .order_by(CheckIn.checkin_date.desc())
        .all()
    )

    return render_template(
        "results.html", user_experiment=latest, summary=summary, checkins=checkins
    )


@app.route("/admin/metrics")
def admin_metrics():
    user = admin_required()
    if not user:
        return redirect(url_for("login"))

    metrics = calculate_admin_metrics()
    return render_template("admin_metrics.html", metrics=metrics)


@app.route("/admin/export/checkins.csv")
def export_checkins_csv():
    user = admin_required()
    if not user:
        return redirect(url_for("login"))

    rows = (
        db.session.query(
            User.email,
            UserExperiment.id,
            Experiment.focus_area,
            Experiment.name,
            UserExperiment.start_date,
            UserExperiment.end_date,
            UserExperiment.completed,
            CheckIn.checkin_date,
            CheckIn.score,
            CheckIn.followed_experiment,
            CheckIn.note,
        )
        .join(UserExperiment, UserExperiment.user_id == User.id)
        .join(Experiment, Experiment.id == UserExperiment.experiment_id)
        .outerjoin(CheckIn, CheckIn.user_experiment_id == UserExperiment.id)
        .order_by(UserExperiment.id.asc(), CheckIn.checkin_date.asc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "email",
            "user_experiment_id",
            "focus_area",
            "experiment_name",
            "experiment_start_date",
            "experiment_end_date",
            "experiment_completed",
            "checkin_date",
            "score",
            "followed_experiment",
            "note",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row.email,
                row.id,
                row.focus_area,
                row.name,
                row.start_date,
                row.end_date,
                row.completed,
                row.checkin_date,
                row.score,
                row.followed_experiment,
                row.note,
            ]
        )

    filename = f"proofloop_checkins_{date.today().isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/admin/export/metrics.csv")
def export_metrics_csv():
    user = admin_required()
    if not user:
        return redirect(url_for("login"))

    metrics = calculate_admin_metrics()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "generated_at_utc",
            "total_users",
            "active_users_today",
            "users_with_1plus_checkin",
            "users_with_5plus_checkins",
            "users_completed_7_days",
            "experiments_started",
            "experiments_completed",
            "avg_score_improvement_pct",
            "retention_day_2_pct",
            "retention_day_7_pct",
            "retention_day_14_pct",
        ]
    )
    writer.writerow(
        [
            metrics["generated_at"].isoformat(),
            metrics["total_users"],
            metrics["active_users_today"],
            metrics["users_with_1plus_checkin"],
            metrics["users_with_5plus_checkins"],
            metrics["users_completed_7_days"],
            metrics["experiments_started"],
            metrics["experiments_completed"],
            (
                round(metrics["avg_score_improvement"], 2)
                if metrics["avg_score_improvement"] is not None
                else ""
            ),
            (
                round(metrics["retention_day_2"]["pct"], 2)
                if metrics["retention_day_2"]["pct"] is not None
                else ""
            ),
            (
                round(metrics["retention_day_7"]["pct"], 2)
                if metrics["retention_day_7"]["pct"] is not None
                else ""
            ),
            (
                round(metrics["retention_day_14"]["pct"], 2)
                if metrics["retention_day_14"]["pct"] is not None
                else ""
            ),
        ]
    )

    filename = f"proofloop_metrics_{date.today().isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/admin/reminders/send", methods=["POST"])
def admin_send_reminders():
    user = admin_required()
    if not user:
        return redirect(url_for("login"))

    dry_run = request.form.get("dry_run") == "1"
    try:
        result = send_daily_reminder_emails(dry_run=dry_run)
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_metrics"))

    mode = "dry run" if dry_run else "send"
    flash(
        f"Reminder {mode}: eligible={result['eligible']} sent={result['sent']} failed={result['failed']}",
        "success",
    )
    return redirect(url_for("admin_metrics"))


@app.cli.command("init-db")
def init_db_command():
    db.create_all()
    seed_experiments()
    print("Database initialized and Energy experiments seeded.")


@app.cli.command("send-daily-reminders")
@click.option("--dry-run", is_flag=True, help="Show how many emails would be sent.")
def send_daily_reminders_command(dry_run: bool):
    result = send_daily_reminder_emails(dry_run=dry_run)
    click.echo(
        "Daily reminder result: "
        f"eligible={result['eligible']} sent={result['sent']} failed={result['failed']} dry_run={result['dry_run']}"
    )


with app.app_context():
    db.create_all()
    seed_experiments()


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
