"""Flow-completion email notifications.

Kept free of Airflow imports so the cluster policy can pull in the callback
without building any DAGs while settings load. Airflow 3 runs DAG callbacks in a
sandbox with no metadata-DB access and no triggering user in the context, so we
read the dag_run row over the SLEDGE_ channel (see pd_store).
"""

import os

from hammer.vlsi import pd_store


def _resolve_notify_email(context, gen_user=None):
    """The address to notify for a finished run, or None to stay quiet.

    On by default: anyone with a registered address gets mail when their run
    finishes. A run triggered with conf {"notify": false} stays quiet. The
    toggle only decides whether to send, never to whom.

    Three sources, in order. SLEDGE_NOTIFY_TO is a deployment-wide override and
    wins outright. Otherwise we try the user who triggered the run, and fall
    back to the user the DAG was generated for. That last one matters inside
    Airflow's task sandbox, where the metadata DB is unreachable and the
    triggering user cannot be looked up -- gen_user is baked into the DAG file,
    so it always resolves.
    """
    dag_run = context.get("dag_run") if isinstance(context, dict) else getattr(context, "dag_run", None)
    dag_id = getattr(dag_run, "dag_id", None)
    run_id = getattr(dag_run, "run_id", None)
    conf = getattr(dag_run, "conf", None)
    if isinstance(conf, dict) and conf.get("notify") is False:
        return None

    override = os.environ.get("SLEDGE_NOTIFY_TO")
    if override:
        return override

    # triggering_user_name is hidden on the runtime proxy, so read it from the row
    uid = getattr(dag_run, "triggering_user_name", None)
    if not uid:
        try:
            uid = pd_store.lookup_triggering_user(dag_id, run_id)
        except Exception as e:
            print(f"[notify] resolve: triggering-user lookup failed: {e}")
            uid = None
    candidates = [u for u in (uid, gen_user) if u and u != "default"]
    if not candidates:
        print(f"[notify] resolve: no user to notify for {dag_id} {run_id}")
        return None
    for who in candidates:
        try:
            addr = pd_store.get_notify_email(who)
        except Exception as e:
            print(f"[notify] resolve: email lookup failed for {who}: {e}")
            continue
        if addr:
            return addr
        print(f"[notify] resolve: {who} has no registered email")
    return None


def _send_completion_email(to, subject, html):
    """Send one notification over SMTP, with the password read from a locked file.

    Host and sender come from SLEDGE_SMTP_* env vars; the password from the file
    at SLEDGE_SMTP_PASSWORD_FILE. A no-op if no sender is configured. We use
    smtplib directly because airflow's send_email only authenticates when an
    smtp_default Connection exists.
    """
    import smtplib
    import ssl
    from email.message import EmailMessage

    user = os.environ.get("SLEDGE_SMTP_USER")
    pw_file = os.environ.get("SLEDGE_SMTP_PASSWORD_FILE")
    if not user or not pw_file:
        print("[notify] no SMTP sender configured (SLEDGE_SMTP_* unset); not sending")
        return
    host = os.environ.get("SLEDGE_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SLEDGE_SMTP_PORT", "587"))
    sender = os.environ.get("SLEDGE_SMTP_FROM") or user
    with open(pw_file) as f:
        password = f.read().strip()

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("Your physical-design flow has finished. See the HTML version of this message for details.")
    msg.add_alternative(html, subtype="html")

    server = smtplib.SMTP(host, port, timeout=30)
    try:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(user, password)
        server.send_message(msg)
    finally:
        server.quit()


def _outcomes_path(dag_id, run_id, create_dir=False):
    import os
    home = os.environ.get("AIRFLOW_HOME") or os.path.expanduser("~/airflow")
    d = os.path.join(home, "stage_outcomes")
    if create_dir:
        os.makedirs(d, exist_ok=True)
    safe = lambda s: "".join(c if c.isalnum() or c in "._-" else "_" for c in str(s))
    return os.path.join(d, f"{safe(dag_id)}__{safe(run_id)}.jsonl")


def record_stage_outcome(dag_id, run_id, task_id, action, state, seconds):
    """Append one stage outcome for the completion email. Never raises."""
    try:
        import json, time
        path = _outcomes_path(dag_id, run_id, create_dir=True)
        with open(path, "a") as f:
            f.write(json.dumps({"ts": time.time(), "task": task_id,
                                "action": action, "state": state,
                                "seconds": round(float(seconds), 1)}) + "\n")
    except Exception as e:
        print(f"[notify] could not record stage outcome: {e}")


def _stage_outcome_lines(dag_id, run_id):
    """The recorded outcomes for one run, oldest first, formatted for the mail."""
    try:
        import json
        rows = []
        with open(_outcomes_path(dag_id, run_id)) as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[notify] could not read stage outcomes: {e}")
        return []
    rows.sort(key=lambda r: r.get("ts") or 0)
    out = []
    for r in rows:
        secs = r.get("seconds") or 0
        if secs >= 5400:
            dur = f"{secs / 3600.0:.1f}h"
        elif secs >= 90:
            dur = f"{secs / 60.0:.0f}m"
        else:
            dur = f"{secs:.0f}s"
        out.append(f"{r.get('task') or r.get('action')}: {r.get('state')} ({dur})")
    return out


def notify_run_finished(context, state, gen_user=None):
    """Email whoever owns this run that it has ended. Called from a task.

    This is the path that actually fires. Airflow 3.1 serializes a DAG without
    any record of its on_success/on_failure callbacks, so the scheduler never
    dispatches them and a DAG-level callback goes silently unrun; see
    notify_flow_complete below. A task with an ALL_DONE trigger rule runs
    however the flow ended, and runs somewhere its output is visible.

    Raises nothing: a mail problem must not fail an otherwise good flow. It
    does say so loudly, because a silent notification failure is what made
    this hard to find the first time.
    """
    dag_run = context.get("dag_run") if isinstance(context, dict) else getattr(context, "dag_run", None)
    dag_id = getattr(dag_run, "dag_id", None) or "unknown"
    run_id = getattr(dag_run, "run_id", None) or "unknown"
    try:
        to = _resolve_notify_email(context, gen_user=gen_user)
        if not to:
            print(f"[notify] {dag_id} {run_id} ({state}): no recipient resolved, not sending")
            return
        subject = f"[Sledgehammer] {dag_id} {state}"
        html = (
            "Your physical-design flow has finished.<br><br>"
            f"DAG: {dag_id}<br>"
            f"Run: {run_id}<br>"
            f"Status: {state}"
        )
        stages = _stage_outcome_lines(dag_id, run_id)
        if stages:
            html += "<br><br>Stages:<br>" + "<br>".join(stages)
        _send_completion_email(to, subject, html)
        print(f"[notify] emailed {to} about {dag_id} {run_id} ({state})")
    except Exception as e:
        print(f"[notify] FAILED to send completion mail for {dag_id} {run_id}: "
              f"{type(e).__name__}: {e}")


def notify_stage_finished(context, task_id, action, state, seconds, gen_user=None):
    """Email that a single stage ended. Called from the stage task itself.

    Complements the end-of-run summary: a long par finishing (or any stage
    failing) is worth knowing about hours before the run's last task sends
    the full report. Never raises, same contract as notify_run_finished.
    """
    dag_run = context.get("dag_run") if isinstance(context, dict) else getattr(context, "dag_run", None)
    dag_id = getattr(dag_run, "dag_id", None) or "unknown"
    run_id = getattr(dag_run, "run_id", None) or "unknown"
    try:
        to = _resolve_notify_email(context, gen_user=gen_user)
        if not to:
            print(f"[notify] {dag_id} {run_id} {task_id} ({state}): no recipient, not sending")
            return
        if seconds >= 5400:
            dur = f"{seconds / 3600.0:.1f}h"
        elif seconds >= 90:
            dur = f"{seconds / 60.0:.0f}m"
        else:
            dur = f"{seconds:.0f}s"
        subject = f"[Sledgehammer] {dag_id} {task_id} {state}"
        html = (
            f"Stage {task_id} finished: {state} ({dur})<br><br>"
            f"DAG: {dag_id}<br>"
            f"Run: {run_id}<br><br>"
            "The full stage summary arrives when the run ends."
        )
        _send_completion_email(to, subject, html)
        print(f"[notify] emailed {to}: {task_id} {state} ({dur})")
    except Exception as e:
        print(f"[notify] FAILED to send stage mail for {dag_id} {run_id} {task_id}: "
              f"{type(e).__name__}: {e}")


def notify_flow_complete(context):
    """DAG-level callback form, kept for older Airflow versions.

    Airflow 3.1 drops callback information when it serializes a DAG, so on that
    version this never runs and notify_run_finished does the work instead.
    """
    dag_run = context.get("dag_run") if isinstance(context, dict) else getattr(context, "dag_run", None)
    state = getattr(dag_run, "state", None) or "finished"
    notify_run_finished(context, state)
