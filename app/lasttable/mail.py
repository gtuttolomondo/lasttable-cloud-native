"""LastTable email templates: inline styles, escaped content, plain-text fallback."""
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from html import escape


def booking_message(recipient, event, sender):
    state = event['state']
    copy = {
        'PENDING': ('PRESA IN CARICO', 'Richiesta ricevuta.', 'presa in carico, in attesa di conferma',
            'La tua richiesta è stata presa in carico dal ristorante ed è in attesa di conferma.',
            'Il tavolo non è ancora confermato. Ti invieremo una seconda email quando il ristorante avrà confermato o rifiutato la richiesta.', '#d9e9a8', '#183d34'),
        'CONFIRMED': ('PRENOTAZIONE CONFERMATA', 'Il tuo tavolo ti aspetta.', 'confermata',
            'Il ristorante ha confermato la tua prenotazione. Ecco tutti i dettagli della tua serata.',
            'La richiesta è stata approvata dal ristorante.', '#d9e9a8', '#183d34'),
        'REJECTED': ('RICHIESTA RIFIUTATA', 'Un’altra serata da scegliere.', 'rifiutata',
            'Il ristorante non ha potuto accogliere la tua richiesta di prenotazione.',
            'Il tavolo non è stato prenotato. Puoi tornare nell’app e scegliere un altro ristorante o un altro orario.', '#f6e0d8', '#893e32'),
    }[state]
    badge, title, outcome, introduction, note, accent, accent_text = copy
    plain = (f"Ciao {recipient['nickname']},\n\n{introduction}\n\n"
             f"Ristorante: {recipient['restaurant_name']}\nPersone: {recipient['party_size']}\n"
             f"Orario: {recipient['reservation_time']}\nCodice: {recipient['public_alias']}\n\n{note}\n\n"
             "Questa è una dimostrazione tecnica: non è una prenotazione reale.\n\nLastTable · From Code to Cloud")
    value = {key: escape(str(recipient[key]), quote=True) for key in
             ['nickname', 'restaurant_name', 'party_size', 'reservation_time', 'public_alias']}
    details = ''.join(
        f'<tr><td style="padding:13px 0;border-bottom:1px solid #e0e4d9;color:#6f7e73;font-size:13px;vertical-align:top;">{label}</td>'
        f'<td align="right" style="padding:13px 0 13px 18px;border-bottom:1px solid #e0e4d9;color:#18352e;font-size:15px;font-weight:bold;overflow-wrap:anywhere;">{value[key]}</td></tr>'
        for label, key in [('Ristorante', 'restaurant_name'), ('Persone', 'party_size'), ('Orario', 'reservation_time')])
    html = f'''<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>LastTable · {badge}</title></head>
<body style="margin:0;padding:0;background:#f7f7f1;color:#18352e;font-family:Arial,Helvetica,sans-serif;">
<div style="display:none;font-size:1px;line-height:1px;color:#f7f7f1;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{introduction}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f7f7f1"><tr><td align="center" style="padding:32px 14px;">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;">
<tr><td style="padding:0 8px 24px;"><span style="font-size:29px;font-weight:bold;letter-spacing:-1.5px;color:#18352e;">lasttable<span style="color:#7b9256;">.</span></span><br><span style="display:inline-block;padding-top:8px;font-size:10px;letter-spacing:2px;color:#6f7e73;">UN TAVOLO. UNA STORIA NEL CLOUD.</span></td></tr>
<tr><td bgcolor="#183d34" style="padding:32px 28px;border-radius:16px 16px 0 0;">
<span style="display:inline-block;padding:8px 12px;border-radius:20px;background:{accent};color:{accent_text};font-size:10px;font-weight:bold;letter-spacing:1.4px;">{badge}</span>
<h1 style="margin:22px 0 12px;font-family:Georgia,'Times New Roman',serif;font-size:36px;line-height:1.15;letter-spacing:-1px;font-weight:normal;color:#fffefa;">{title}</h1>
<p style="margin:0;font-size:14px;line-height:1.8;color:#e0e8d8;">{introduction}</p></td></tr>
<tr><td bgcolor="#fffefa" style="padding:28px;border:1px solid #e0e4d9;border-top:0;border-radius:0 0 16px 16px;">
<p style="margin:0 0 22px;font-size:16px;line-height:1.6;">Ciao <strong>{value['nickname']}</strong>,</p>
<p style="margin:0 0 10px;font-size:10px;letter-spacing:2px;color:#6f7e73;">IL TUO TAVOLO</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">{details}</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:22px;"><tr><td bgcolor="#f0f3e7" style="padding:16px;border-radius:8px;"><span style="font-size:10px;letter-spacing:1.2px;color:#6f7e73;">CODICE PRENOTAZIONE</span><br><strong style="display:inline-block;padding-top:8px;font-size:18px;letter-spacing:1px;color:#18352e;">{value['public_alias']}</strong></td></tr></table>
<p style="margin:24px 0 0;font-size:13px;line-height:1.8;color:#52685c;">{note}</p>
<p style="margin:26px 0 0;padding-top:18px;border-top:1px solid #e0e4d9;font-size:11px;line-height:1.8;color:#6f7e73;">Demo illustrativa: nessuna prenotazione reale.<br>Questa email mostra il flusso di un’applicazione cloud.</p>
</td></tr><tr><td align="center" style="padding:24px 16px;font-size:11px;line-height:1.8;color:#6f7e73;">LastTable · From Code to Cloud<br><span style="font-size:10px;">Powered by Oracle Cloud Infrastructure</span></td></tr>
</table></td></tr></table></body></html>'''
    message = EmailMessage(policy=SMTP)
    message['From'] = sender
    message['To'] = recipient['recipient_email']
    message['Subject'] = f"LastTable · Prenotazione {recipient['public_alias']} {outcome}"
    message['Message-ID'] = f"<lasttable.{event['event_id']}@{sender.split('@')[1]}>"
    message['Date'] = formatdate(usegmt=True)
    message.set_content(plain)
    message.add_alternative(html, subtype='html')
    return message
