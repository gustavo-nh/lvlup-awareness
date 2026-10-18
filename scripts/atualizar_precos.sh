#!/usr/bin/env bash
# Dispara o workflow de precos (precos.yml) e espera ele terminar (ate ~4 min).
# Usado pelo agente no inicio de cada briefing/varredura, porque o agendamento
# horario do GitHub nao e confiavel. Nunca falha: em qualquer erro, avisa e
# segue com o precos.json atual.
REPO="gustavo-nh/lvlup-awareness"
WF="precos.yml"
desde=$(date -u -d '-60 sec' +%Y-%m-%dT%H:%M:%SZ)   # tolera diferenca de relogio

if ! gh api -X POST "repos/$REPO/actions/workflows/$WF/dispatches" -f ref=main >/dev/null 2>&1; then
  echo "AVISO: nao consegui disparar a atualizacao de precos; seguindo com o precos.json atual"
  exit 0
fi

for _ in $(seq 1 24); do
  sleep 10
  linha=$(gh api "repos/$REPO/actions/workflows/$WF/runs?event=workflow_dispatch&per_page=1" \
          --jq '.workflow_runs[0] | "\(.created_at) \(.status) \(.conclusion)"' 2>/dev/null)
  criado=${linha%% *}
  if [[ -n "$criado" && ! "$criado" < "$desde" && "$linha" == *" completed "* ]]; then
    echo "precos atualizados: $linha"
    git pull -q --rebase origin main || true
    exit 0
  fi
done

echo "AVISO: a atualizacao de precos nao terminou a tempo; seguindo com o precos.json atual"
git pull -q --rebase origin main || true
exit 0
