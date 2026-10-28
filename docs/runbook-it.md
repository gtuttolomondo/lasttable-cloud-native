# LastTable: copione della demo

## Preparazione

Aprire la console OCI sul compartment della demo, il repository OCI privato, le pipeline DevOps e l'applicazione HTTPS. Tenere `/demo` sullo schermo condiviso e `/presenter` in una finestra del relatore. Recuperare il token relatore da Vault in una sessione privata: non inserirlo nell'URL, nelle slide o nei comandi registrati.

Verificare nodi Ready, rollout completati, ultimo refresh della credenziale OCIR riuscito, snapshot Kafka recente e lag dei tre gruppi a zero. Verificare DNS, certificato HTTPS, SPF, DKIM e mittente approvato. Fare una prenotazione di prova solo verso la casella concordata e controllare separatamente SMTP e ricezione.

La prima invocazione della Function può richiedere tempo: nel collaudo una chiamata reale da OKE ha impiegato 53 secondi. Il consumer attende fino a 90 secondi. Prima del webinar eseguire il self-test della Function, che controlla database e autenticazione SMTP senza inviare messaggi; l'apertura della pagina web non avvia la Function. In `/demo` osservare il lag durante l'attesa, senza creare nuove prenotazioni per ritentare la stessa notifica.

Prima dell'evento creare una nuova sessione da `/presenter`, dopo che gli eventi precedenti sono stati smaltiti. Non cancellare offset Kafka per azzerare i contatori: la sessione separa i dati della dimostrazione. Tenere una casella del relatore disponibile per mostrare una notifica senza condividere gli indirizzi dei partecipanti.

Il provisioning completo richiede attese incompatibili con una dimostrazione continua di 50 minuti. Predisporre l'ambiente e registrare i passaggi lunghi: mostrare esplicitamente i tagli e i tempi reali. Il cluster Kafka del primo collaudo ha impiegato circa 35 minuti; il tempo non è garantito per le creazioni successive.

Preparare anche IAM in anticipo: Oracle indica circa un'ora per propagare modifiche alle regole dei dynamic group. Prima della prima build verificare l'accesso in scrittura a OCIR. Installare la foundation Kubernetes come operatore: la pipeline può aggiornare i soli oggetti applicativi e non può leggere Secret o modificare RBAC. Riferimento: [aggiornamento dei dynamic group](https://docs.oracle.com/en-us/iaas/Content/Identity/dynamicgroups/Updating_Dynamic_Groups.htm).

## Sequenza da 60 minuti

| Minuti | Parte | Cosa mostrare |
|---|---|---|
| 0–5 | Contesto | Prenotazione dal telefono, evento durevole, servizi indipendenti |
| 5–13 | Infrastruttura come pacchetto | ZIP, schema Resource Manager, Plan e Apply registrato, risorse reali |
| 13–22 | Codice e immagini | Componenti, test Podman, repository OCIR privati e digest |
| 22–32 | CI/CD | Build DevOps, test, pubblicazione e deploy Function/OKE; release nell'interfaccia |
| 32–40 | Partecipazione | QR, una prenotazione e traccia correlata in `/demo` |
| 40–49 | Eventi e notifiche | Pausa consumer, conferme che continuano, lag, ripresa e notifica |
| 49–55 | Replay | Nuova proiezione isolata, confronto con classifica live, nessuna nuova email |
| 55–60 | Domande | Kafka, consumer su OKE, Function per le notifiche e significato degli esiti SMTP |

Le build e i deploy reali possono superare il tempo assegnato al blocco. Tenere pronta la registrazione di un'esecuzione completa e la release già funzionante. Il tempo rimanente serve a osservare il flusso, non ad attendere il provisioning.

## Percorso di una prenotazione

1. Il telefono invia un UUID di richiesta. L'API salva prenotazione PENDING e outbox nella stessa transazione Oracle.
2. Il publisher invia `booking.created` e attende l'ACK del broker prima di marcare l'outbox.
3. Il consumer registra la richiesta in attesa. In **Gestisci Tavoli** (`/manage`) l’amministratore conferma o rifiuta; stato finale e `booking.status_changed` sono salvati nella stessa transazione. Il publisher pubblica l’evento e la Function notifica l’esito.
4. Il consumer classifica usa un gruppo indipendente e conta gli eventi creati unici.
5. Il consumer notifiche legge il cambio di stato e invoca sincronicamente la Function. L'email viene risolta nel database: non è contenuta nel messaggio Kafka.
6. La Function usa il registro di idempotenza e invia tramite SMTP autenticato con TLS. Il consumer avanza l'offset dopo l'esito durevole.

La conferma della prenotazione non dipende dall'invio dell'email. Un guasto delle notifiche può accumulare lag senza bloccare conferme e classifica.

## Cosa leggere in `/demo`

- Ricevute e confermate: conteggi nel database per la sessione corrente.
- Email accettate: registro notifiche in stato SUBMITTED, che indica accettazione SMTP.
- Outbox: eventi della sessione ancora da pubblicare.
- Lag: differenza tra fine delle partizioni e offset committati dai tre gruppi, rilevata tramite Kafka Admin API. Non rappresenta una coda OCI Queue.
- Classifica: proiezione del consumer trending, separata dalle conferme.
- Eventi: ultimi 100 passaggi sanitizzati, con alias, correlation ID, partizione e offset. La vista non espone destinatari o nickname.
- Timestamp: controllare l'età dello snapshot prima di interpretare i numeri. Le fonti vengono campionate in momenti diversi: i contatori non devono coincidere in ogni istante.

## Pausa e ripresa

Da `/presenter` richiedere la pausa e attendere l'indicazione PAUSED del consumer notifiche. Fare una prenotazione verso la casella del relatore. In Gestisci Tavoli confermare la richiesta; mostrare CONFIRMED, classifica aggiornata e lag notifiche maggiore di zero. Riprendere il consumer, attendere il recupero del lag e mostrare SUBMITTED. Controllare la ricezione nella casella separatamente.

SUBMITTED non prova recapito o lettura. FAILED indica un tentativo fallito con retry limitato. UNKNOWN indica una risposta SMTP ambigua: il sistema evita il reinvio automatico e richiede riconciliazione. Non provocare un errore SMTP incerto durante l'evento se non è pronto un esempio controllato e già collaudato.

## Replay

Attendere che trending sia aggiornato, poi avviare il replay da `/presenter`. La richiesta cattura una soglia di offset per ogni partizione e ricostruisce una proiezione distinta con un nuovo gruppo. Al completamento confrontare `expected_json` e `actual_json`. Il replay legge gli eventi di creazione della sessione e non segue il ramo delle notifiche.

Se la retention non conserva tutta la storia, il confronto completo deve fallire esplicitamente. Se un processo replay viene interrotto, la richiesta viene marcata FAILED al riavvio del singolo worker; si può avviare una nuova ricostruzione.

## Rilascio e recupero

Il rilascio di sviluppo usa RollingUpdate per l’API; le altre repliche singole usano Recreate: durante il rilascio è previsto un breve intervallo di indisponibilità. Attendere il completamento del deploy e la readiness di tutti i workload prima del collaudo o della partecipazione del pubblico.

Mostrare commit sorgente, test superati, tag univoci e digest autorevoli OCIR. La pipeline passa i digest al deploy; il backend espone la release in `/api/health` e nell'interfaccia. Per un recupero usare la stessa pipeline con i digest della release precedente e una nuova etichetta operativa, mantenendo le migrazioni compatibili. Terraform non deve ripristinare l'immagine della Function gestita da DevOps.

Prima di ritentare una prenotazione usare lo stesso UUID: un nuovo UUID rappresenta una nuova richiesta e può produrre una nuova email. I test automatici HTTPS conservano l'UUID sotto `.local/` proprio per evitare invii duplicati durante i rilanci del collaudo.

## Pulizia

Rimuovere soltanto l'ambiente di verifica temporaneo dopo i controlli. Fermare le pipeline, rimuovere consapevolmente Service LoadBalancer e PVC, verificare il distacco dell'IP riservato, quindi esaminare il Destroy dello stack corretto. Per l'ambiente principale concordare il termine della demo prima della rimozione. Il compartment preesistente non appartiene allo stack.

## Regia operativa aggiornata

In `/demo` i contatori in alto si riferiscono alla sessione corrente. Topic, partizioni, membri dei gruppi, offset e lag sono invece dell’intero cluster condiviso. La lettura Kafka non rimuove i record; il range di offset conservato non viene presentato come un conteggio esatto dei messaggi.

Selezionare un codice anonimo per mostrare i passaggi della richiesta, l’email di presa in carico, la decisione manuale e l’email finale. I tempi separano la gestione tecnica dall’attesa umana; le prenotazioni antecedenti alle nuove funzioni mostrano i passaggi non rilevati come tali.

I grafici mostrano pubblicazioni/email per minuto della sessione e lag del cluster. Durante la pausa notifiche, la classifica può continuare ad avanzare. L’attesa dell’amministratore non viene sommata al lag Kafka. Le nuove attività illuminano le componenti coinvolte, senza animazioni in assenza di nuovi eventi.
