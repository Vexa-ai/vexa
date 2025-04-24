#recreate bd
docker compose exec admin-api python /app/app/scripts/recreate_db.py

#rebuild vexa-bot
make build-bot-image


#

docker compose down && docker compose up -d --build



now you have full acces to edit files and run terminal commands, go ahead