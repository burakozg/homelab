# Serves the static governance site. The pages are NOT baked in — they are bind
# mounted from the NAS and replaced by ./deploy publish after every collector
# run, so a refresh never restarts anything.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
