from __future__ import annotations

LANGUAGES = ["en", "es", "fr", "de", "it", "pt", "ru"]

DOMAINS = [
    "general",
    "healthcare",
    "finance",
    "legal",
    "software",
    "sales",
    "manufacturing",
    "silence",  # Silence domain: tests silence detection with various background noises
]

# Keep phrases short-ish for fast tests. Prefer punctuation-light sentences.
PHRASES: dict[str, list[str]] = {
    "en": [
        "Hello, this is a quality test for the transcription service.",
        "Please confirm that you can hear this sentence clearly.",
        "The quick brown fox jumps over the lazy dog.",
        "We are testing speech recognition with noise and silence.",
        "Numbers: one two three four five.",
    ],
    "es": [
        "Hola, esta es una prueba de calidad del servicio de transcripción.",
        "Por favor confirma que puedes oír esta frase con claridad.",
        "El zorro marrón rápido salta sobre el perro perezoso.",
        "Estamos probando el reconocimiento de voz con ruido y silencio.",
        "Números: uno dos tres cuatro cinco.",
    ],
    "fr": [
        "Bonjour, ceci est un test de qualité du service de transcription.",
        "Merci de confirmer que vous entendez cette phrase clairement.",
        "Le vif renard brun saute par-dessus le chien paresseux.",
        "Nous testons la reconnaissance vocale avec du bruit et du silence.",
        "Nombres: un deux trois quatre cinq.",
    ],
    "de": [
        "Hallo, dies ist ein Qualitätstest für den Transkriptionsdienst.",
        "Bitte bestätige, dass du diesen Satz klar hören kannst.",
        "Der schnelle braune Fuchs springt über den faulen Hund.",
        "Wir testen Spracherkennung mit Rauschen und Stille.",
        "Zahlen: eins zwei drei vier fünf.",
    ],
    "it": [
        "Ciao, questo è un test di qualità per il servizio di trascrizione.",
        "Per favore conferma che puoi sentire chiaramente questa frase.",
        "La veloce volpe marrone salta sopra il cane pigro.",
        "Stiamo testando il riconoscimento vocale con rumore e silenzio.",
        "Numeri: uno due tre quattro cinque.",
    ],
    "pt": [
        "Olá, este é um teste de qualidade para o serviço de transcrição.",
        "Por favor confirme que você consegue ouvir esta frase claramente.",
        "A rápida raposa marrom pula sobre o cão preguiçoso.",
        "Estamos testando reconhecimento de fala com ruído e silêncio.",
        "Números: um dois três quatro cinco.",
    ],
    "ru": [
        "Привет, это тест качества для сервиса транскрипции.",
        "Пожалуйста, подтвердите, что вы ясно слышите это предложение.",
        "Быстрая коричневая лиса перепрыгивает через ленивую собаку.",
        "Мы тестируем распознавание речи с шумом и тишиной.",
        "Числа: один два три четыре пять.",
    ],
}

PHRASES_BY_DOMAIN: dict[str, dict[str, list[str]]] = {
    "en": {
        "general": PHRASES["en"],
        "healthcare": [
            "The patient reports chest pain and shortness of breath.",
            "Please schedule a follow up appointment next week.",
            "Medication dosage is five milligrams twice a day.",
            "Vital signs are stable and oxygen saturation is normal.",
        ],
        "finance": [
            "Revenue increased this quarter but expenses also grew.",
            "Please approve the invoice and update the budget forecast.",
            "The interest rate changed and the loan payment is higher.",
            "We need to reconcile the statements and close the month.",
        ],
        "legal": [
            "Please review the contract and confirm the termination clause.",
            "The agreement includes confidentiality and liability terms.",
            "We need legal approval before we sign this document.",
            "The policy requires written consent from both parties.",
        ],
        "software": [
            "Deploy the new release to staging and run the smoke tests.",
            "The API returned an error so we need to check the logs.",
            "Please open a ticket and assign it to the backend team.",
            "The database migration failed and we need a rollback plan.",
        ],
        "sales": [
            "The customer asked for a discount and a new proposal.",
            "Please follow up with the lead and schedule a demo.",
            "We need to renew the subscription before the deadline.",
            "Pipeline is strong but conversion rate is lower this week.",
        ],
        "manufacturing": [
            "The production line stopped due to a sensor fault.",
            "Quality control found defects in the latest batch.",
            "Please check inventory and reorder raw materials.",
            "The shipment is delayed and the schedule must be updated.",
        ],
        "silence": [],  # Silence domain has no phrases - only silence cases
    },
    "es": {
        "general": PHRASES["es"],
        "healthcare": [
            "El paciente refiere dolor en el pecho y falta de aire.",
            "Por favor programe una cita de seguimiento la próxima semana.",
            "La dosis del medicamento es de cinco miligramos dos veces al día.",
            "Los signos vitales son estables y la saturación es normal.",
        ],
        "finance": [
            "Los ingresos subieron este trimestre pero también aumentaron los gastos.",
            "Por favor apruebe la factura y actualice el presupuesto.",
            "La tasa de interés cambió y el pago del préstamo es mayor.",
            "Necesitamos conciliar los estados y cerrar el mes.",
        ],
        "legal": [
            "Por favor revise el contrato y confirme la cláusula de rescisión.",
            "El acuerdo incluye confidencialidad y responsabilidad.",
            "Necesitamos aprobación legal antes de firmar este documento.",
            "La política requiere consentimiento escrito de ambas partes.",
        ],
        "software": [
            "Despliegue la nueva versión en pruebas y ejecute las pruebas rápidas.",
            "La API devolvió un error y debemos revisar los registros.",
            "Abra un ticket y asígnelo al equipo de backend.",
            "Falló la migración de la base de datos y necesitamos un plan de reversión.",
        ],
        "sales": [
            "El cliente pidió un descuento y una nueva propuesta.",
            "Haga seguimiento del cliente potencial y programe una demostración.",
            "Debemos renovar la suscripción antes de la fecha límite.",
            "El embudo está fuerte pero la conversión bajó esta semana.",
        ],
        "manufacturing": [
            "La línea de producción se detuvo por una falla del sensor.",
            "Control de calidad encontró defectos en el último lote.",
            "Verifique el inventario y reordene materias primas.",
            "El envío se retrasó y debemos actualizar el cronograma.",
        ],
    },
    "fr": {
        "general": PHRASES["fr"],
        "healthcare": [
            "Le patient signale une douleur thoracique et un essoufflement.",
            "Veuillez planifier un rendez vous de suivi la semaine prochaine.",
            "La dose du médicament est de cinq milligrammes deux fois par jour.",
            "Les signes vitaux sont stables et la saturation est normale.",
        ],
        "finance": [
            "Les revenus ont augmenté ce trimestre mais les dépenses aussi.",
            "Veuillez valider la facture et mettre à jour le budget.",
            "Le taux d intérêt a changé et le paiement du prêt est plus élevé.",
            "Nous devons rapprocher les relevés et clôturer le mois.",
        ],
        "legal": [
            "Veuillez relire le contrat et confirmer la clause de résiliation.",
            "L accord inclut des conditions de confidentialité et de responsabilité.",
            "Nous avons besoin d une validation juridique avant de signer.",
            "La politique exige un consentement écrit des deux parties.",
        ],
        "software": [
            "Déployez la nouvelle version en recette et lancez les tests rapides.",
            "L API a renvoyé une erreur et nous devons vérifier les logs.",
            "Ouvrez un ticket et assignez le à l équipe backend.",
            "La migration de la base a échoué et il faut un plan de retour.",
        ],
        "sales": [
            "Le client a demandé une remise et une nouvelle proposition.",
            "Merci de relancer le prospect et de planifier une démo.",
            "Nous devons renouveler l abonnement avant la date limite.",
            "Le pipeline est bon mais le taux de conversion baisse cette semaine.",
        ],
        "manufacturing": [
            "La ligne de production s est arrêtée à cause d un capteur défaillant.",
            "Le contrôle qualité a trouvé des défauts dans le dernier lot.",
            "Veuillez vérifier le stock et recommander les matières premières.",
            "L expédition est en retard et le planning doit être mis à jour.",
        ],
    },
    "de": {
        "general": PHRASES["de"],
        "healthcare": [
            "Der Patient berichtet über Brustschmerzen und Atemnot.",
            "Bitte vereinbaren Sie einen Kontrolltermin für nächste Woche.",
            "Die Medikamentendosis beträgt fünf Milligramm zweimal täglich.",
            "Die Vitalwerte sind stabil und die Sauerstoffsättigung ist normal.",
        ],
        "finance": [
            "Der Umsatz ist dieses Quartal gestiegen aber die Kosten auch.",
            "Bitte genehmigen Sie die Rechnung und aktualisieren Sie das Budget.",
            "Der Zinssatz hat sich geändert und die Rate ist höher.",
            "Wir müssen die Auszüge abstimmen und den Monat abschließen.",
        ],
        "legal": [
            "Bitte prüfen Sie den Vertrag und bestätigen Sie die Kündigungsklausel.",
            "Die Vereinbarung enthält Vertraulichkeit und Haftungsbedingungen.",
            "Wir brauchen eine juristische Freigabe vor der Unterschrift.",
            "Die Richtlinie erfordert eine schriftliche Zustimmung beider Parteien.",
        ],
        "software": [
            "Bitte deploye das Release auf Staging und führe Smoke Tests aus.",
            "Die API hat einen Fehler geliefert wir müssen die Logs prüfen.",
            "Erstelle ein Ticket und weise es dem Backend Team zu.",
            "Die Datenbankmigration ist fehlgeschlagen wir brauchen einen Rollback Plan.",
        ],
        "sales": [
            "Der Kunde möchte einen Rabatt und ein neues Angebot.",
            "Bitte melde dich beim Lead und plane eine Demo.",
            "Wir müssen das Abonnement vor der Frist verlängern.",
            "Die Pipeline ist gut aber die Conversion Rate ist niedriger diese Woche.",
        ],
        "manufacturing": [
            "Die Produktionslinie stand wegen eines Sensorfehlers still.",
            "Die Qualitätskontrolle hat Defekte in der letzten Charge gefunden.",
            "Bitte prüfe den Bestand und bestelle Rohstoffe nach.",
            "Die Lieferung verzögert sich und der Plan muss aktualisiert werden.",
        ],
    },
    "it": {
        "general": PHRASES["it"],
        "healthcare": [
            "Il paziente riferisce dolore al petto e mancanza di respiro.",
            "Per favore prenota una visita di controllo per la prossima settimana.",
            "La dose del farmaco è cinque milligrammi due volte al giorno.",
            "I parametri vitali sono stabili e la saturazione è normale.",
        ],
        "finance": [
            "I ricavi sono aumentati questo trimestre ma anche i costi.",
            "Per favore approva la fattura e aggiorna il budget.",
            "Il tasso di interesse è cambiato e la rata è più alta.",
            "Dobbiamo riconciliare gli estratti e chiudere il mese.",
        ],
        "legal": [
            "Per favore rivedi il contratto e conferma la clausola di recesso.",
            "L accordo include riservatezza e responsabilità.",
            "Serve approvazione legale prima di firmare questo documento.",
            "La policy richiede consenso scritto da entrambe le parti.",
        ],
        "software": [
            "Distribuisci la nuova release in staging ed esegui gli smoke test.",
            "L API ha restituito un errore e dobbiamo controllare i log.",
            "Apri un ticket e assegnalo al team backend.",
            "La migrazione del database è fallita e serve un piano di rollback.",
        ],
        "sales": [
            "Il cliente ha chiesto uno sconto e una nuova proposta.",
            "Per favore segui il contatto e pianifica una demo.",
            "Dobbiamo rinnovare l abbonamento prima della scadenza.",
            "La pipeline è forte ma il tasso di conversione è più basso questa settimana.",
        ],
        "manufacturing": [
            "La linea di produzione si è fermata per un guasto del sensore.",
            "Il controllo qualità ha trovato difetti nell ultimo lotto.",
            "Controlla le scorte e riordina le materie prime.",
            "La spedizione è in ritardo e il programma va aggiornato.",
        ],
    },
    "pt": {
        "general": PHRASES["pt"],
        "healthcare": [
            "O paciente relata dor no peito e falta de ar.",
            "Por favor agende um retorno para a próxima semana.",
            "A dose do medicamento é cinco miligramas duas vezes ao dia.",
            "Os sinais vitais estão estáveis e a saturação está normal.",
        ],
        "finance": [
            "A receita aumentou neste trimestre mas as despesas também cresceram.",
            "Por favor aprove a fatura e atualize o orçamento.",
            "A taxa de juros mudou e a parcela ficou mais alta.",
            "Precisamos conciliar os extratos e fechar o mês.",
        ],
        "legal": [
            "Por favor revise o contrato e confirme a cláusula de rescisão.",
            "O acordo inclui confidencialidade e responsabilidade.",
            "Precisamos de aprovação jurídica antes de assinar este documento.",
            "A política exige consentimento por escrito de ambas as partes.",
        ],
        "software": [
            "Faça o deploy da nova versão em staging e rode os smoke tests.",
            "A API retornou erro e precisamos checar os logs.",
            "Abra um ticket e atribua ao time de backend.",
            "A migração do banco falhou e precisamos de um plano de rollback.",
        ],
        "sales": [
            "O cliente pediu desconto e uma nova proposta.",
            "Por favor faça follow up do lead e marque uma demo.",
            "Precisamos renovar a assinatura antes do prazo.",
            "O pipeline está bom mas a conversão caiu esta semana.",
        ],
        "manufacturing": [
            "A linha de produção parou por falha no sensor.",
            "O controle de qualidade encontrou defeitos no último lote.",
            "Verifique o estoque e reponha matérias primas.",
            "O envio atrasou e o cronograma precisa ser atualizado.",
        ],
    },
    "ru": {
        "general": PHRASES["ru"],
        "healthcare": [
            "Пациент жалуется на боль в груди и одышку.",
            "Пожалуйста запланируйте повторный прием на следующей неделе.",
            "Дозировка лекарства пять миллиграммов два раза в день.",
            "Жизненные показатели стабильны и сатурация в норме.",
        ],
        "finance": [
            "Выручка выросла в этом квартале но расходы тоже увеличились.",
            "Пожалуйста утвердите счет и обновите бюджетный прогноз.",
            "Процентная ставка изменилась и платеж по кредиту выше.",
            "Нужно сверить выписки и закрыть месяц.",
        ],
        "legal": [
            "Пожалуйста проверьте договор и пункт о расторжении.",
            "Соглашение включает условия конфиденциальности и ответственности.",
            "Нужно юридическое согласование перед подписанием документа.",
            "Политика требует письменного согласия обеих сторон.",
        ],
        "software": [
            "Разверните релиз на стенд и запустите smoke тесты.",
            "API вернул ошибку и нужно проверить логи.",
            "Создайте тикет и назначьте его команде backend.",
            "Миграция базы данных не удалась и нужен план отката.",
        ],
        "sales": [
            "Клиент попросил скидку и новую коммерческую заявку.",
            "Пожалуйста сделайте фоллоу ап и назначьте демонстрацию.",
            "Нужно продлить подписку до крайнего срока.",
            "Воронка сильная но конверсия ниже на этой неделе.",
        ],
        "manufacturing": [
            "Производственная линия остановилась из за сбоя датчика.",
            "Контроль качества нашел дефекты в последней партии.",
            "Проверьте склад и закажите сырье.",
            "Отгрузка задерживается и график нужно обновить.",
        ],
    },
}


