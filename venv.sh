# This file's own path: BASH_SOURCE in bash, the %x prompt escape in zsh
# (eval'd so bash never parses the zsh-only expansion).
if [ -n "${ZSH_VERSION:-}" ]; then
    eval '_sledge_src="${(%):-%x}"'
else
    _sledge_src="${BASH_SOURCE[0]}"
fi
_sledge_repo="$(CDPATH= cd -- "$(dirname -- "$_sledge_src")" && pwd)"
unset _sledge_src

# Ensure uv and pg_config are on PATH, behind the venv activated next
export PATH="$HOME/pg_local/usr/bin:$HOME/.local/bin:$PATH"

if [ ! -f "$_sledge_repo/.venv/bin/activate" ]; then
    echo "[sledge] no venv at $_sledge_repo/.venv; run scripts/uv_setup.sh" >&2
    unset _sledge_repo
    return 1
fi
source "$_sledge_repo/.venv/bin/activate"

# Set AIRFLOW_HOME to this checkout (Hammer root)
export AIRFLOW_HOME="$_sledge_repo"
unset _sledge_repo

# Source BWRC environment for EDA tools (VCS, Genus, Innovus, etc.)
if [ -f /tools/C/ee290-sp25/bwrc-env.sh ]; then
    source /tools/C/ee290-sp25/bwrc-env.sh
    echo "BWRC EDA environment sourced."
fi

# RHEL 9 workaround: Cadence tools need libnsl.so.1 (removed in RHEL 9)
if [ -f "$HOME/libnsl_local/usr/lib64/libnsl.so.1" ]; then
    export LD_LIBRARY_PATH="$HOME/libnsl_local/usr/lib64:${LD_LIBRARY_PATH:-}"
fi

echo "Virtual environment activated."
echo "AIRFLOW_HOME set to: $AIRFLOW_HOME"

# Real DB/SMTP secrets (incl. HAMMER_PG_PASSWORD) are GPG-encrypted in
# .sledgehammer/airflow-secrets.env.gpg; airflow.cfg ships a blank conn. Decrypt
# once and export into the shell so airflow and studio reach Postgres without a
# separate step. No-op once loaded; a cancelled or failed decrypt is non-fatal.
_sledge_load_secrets() {
    [ -n "${AIRFLOW__DATABASE__SQL_ALCHEMY_CONN:-}" ] && return 0
    local _enc="${AIRFLOW_HOME:-$(pwd)}/.sledgehammer/airflow-secrets.env.gpg"
    [ -f "$_enc" ] || return 0
    echo "[sledge] loading secrets (enter your GPG passphrase if asked) ..." >&2
    local _line _k _v
    while IFS= read -r _line; do
        case "$_line" in ''|\#*) continue ;; esac
        _line="${_line#export }"; _k="${_line%%=*}"; _v="${_line#*=}"
        _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
        export "$_k=$_v"
    done < <(gpg --quiet --no-symkey-cache --decrypt "$_enc" 2>/dev/null)
    [ -z "${AIRFLOW__DATABASE__SQL_ALCHEMY_CONN:-}" ] && \
        echo "[sledge] secrets not loaded (cancelled or wrong passphrase); rerun 'source venv.sh' to retry." >&2
    return 0
}

# Foreign OpenSSL guard. Conda envs (chipyard's included) ship their own
# libcrypto.so.3 that lacks symbols the system libldap needs (EVP_md2 on
# RHEL 9.7), which kills psycopg2 with "undefined symbol: EVP_md2". A
# chipyard/conda activation leaves its lib dir on LD_LIBRARY_PATH even after
# conda deactivate, so strip conda-shaped entries here.
if [ -n "${LD_LIBRARY_PATH:-}" ]; then
    _sledge_clean=""
    _sledge_dropped=""
    # One entry per line through a heredoc, not `for _p in $LD_LIBRARY_PATH`:
    # zsh does not word-split, so that loop saw the whole value as one entry
    # and a single conda dir dropped every other entry with it.
    while IFS= read -r _p; do
        case "$_p" in
            *[Cc]onda*|"${CONDA_PREFIX:-/nonexistent-conda}"/*)
                _sledge_dropped="$_sledge_dropped $_p" ;;
            *)
                _sledge_clean="${_sledge_clean:+$_sledge_clean:}$_p" ;;
        esac
    done <<_SLEDGE_EOF
$(printf '%s\n' "$LD_LIBRARY_PATH" | tr ':' '\n')
_SLEDGE_EOF
    if [ -n "$_sledge_dropped" ]; then
        export LD_LIBRARY_PATH="$_sledge_clean"
        echo "[sledge] removed conda entries from LD_LIBRARY_PATH (their libcrypto.so.3"
        echo "         breaks the system libldap, which breaks psycopg2):$_sledge_dropped"
    fi
    unset _sledge_clean _sledge_dropped _p
fi

# Preflight: if psycopg2 still cannot load, explain why in one screen instead
# of letting airflow die in a 30-line traceback ending at the real cause.
if ! python3 -c "import psycopg2" >/dev/null 2>&1; then
    _sledge_err=$(python3 -c "import psycopg2" 2>&1 | tail -1)
    echo "[sledge] WARNING: psycopg2 cannot load; airflow and studio will fail." >&2
    echo "         $_sledge_err" >&2
    case "$_sledge_err" in
        *"undefined symbol"*|*libcrypto*|*libssl*|*libldap*)
            echo "         This is a library conflict: your environment loads a foreign" >&2
            echo "         OpenSSL ahead of the system one. Likely causes:" >&2
            [ -n "${CONDA_PREFIX:-}" ] &&                 echo "           - active conda env: $CONDA_PREFIX  (fix: conda deactivate, open a fresh shell)" >&2
            while IFS= read -r _p; do
                [ -n "$_p" ] && [ -e "$_p/libcrypto.so.3" ] &&                     echo "           - LD_LIBRARY_PATH entry shipping its own libcrypto.so.3: $_p" >&2
            done <<_SLEDGE_EOF
$(printf '%s\n' "${LD_LIBRARY_PATH:-}" | tr ':' '\n')
_SLEDGE_EOF
            unset _p
            _sledge_pg=$(ls "$VIRTUAL_ENV"/lib/python*/site-packages/psycopg2/_psycopg*.so 2>/dev/null | head -1)
            if [ -n "$_sledge_pg" ]; then
                _sledge_rp=$(readelf -d "$_sledge_pg" 2>/dev/null | grep -E "RPATH|RUNPATH" | grep -io "conda[^]]*")
                if [ -n "$_sledge_rp" ]; then
                    echo "           - psycopg2 itself was BUILT under conda: its binary carries" >&2
                    echo "             a conda RPATH ($_sledge_rp), so no shell cleanup can fix it." >&2
                    echo "             Rebuild it from a conda-free shell:" >&2
                    echo "                 conda deactivate   # repeat until (base) is gone too" >&2
                    echo "                 ./scripts/uv_setup.sh" >&2
                fi
            fi
            unset _sledge_pg _sledge_rp
            echo "         Fix: rerun from a clean login shell (no conda / chipyard env" >&2
            echo "         sourced), or remove the entries above from LD_LIBRARY_PATH." >&2
            ;;
    esac
    unset _sledge_err
fi

# Load them up front when a person sources this. Skipped without a tty (scripts,
# cron) so nothing blocks on a passphrase prompt; opt out with SLEDGE_NO_AUTO_SECRETS=1.
# The launcher skips its own decrypt when the conn is already set, so one prompt.
if [ -t 0 ] && [ -z "${SLEDGE_NO_AUTO_SECRETS:-}" ]; then
    _sledge_load_secrets
fi

# Bare `airflow` still loads them lazily in case the up-front step was skipped,
# then runs the real binary. Subprocesses use the real binary directly.
airflow() {
    _sledge_load_secrets
    command airflow "$@"
}
