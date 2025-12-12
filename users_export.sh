#/bin/bash
ssh -p 22222 -i ~/.ssh/id_lottina_db service_zrsl3emrq9oe@default-server-0nivz5.sliplane.app  \
"pg_dump -U lottina_admin --table=users --data-only --column-inserts lottina" \
> users_export.sql


