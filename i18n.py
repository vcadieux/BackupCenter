"""Application message localization. User data and SQL/SSH identifiers are never translated."""
import json
from pathlib import Path
from flask import request
MESSAGES=json.loads(Path(__file__).with_name('messages.json').read_text())
PREFIXES={
 'Champ invalide : ':'Invalid field: ',
 'Serveur ajouté : ':'Server added: ',
 'Serveur modifié : ':'Server updated: ',
 'Source ajoutée : ':'Source added: ',
 'Source modifiée : ':'Source updated: ',
 'Empreinte SSH confirmée pour ':'SSH fingerprint confirmed for ',
 'Sites détectés pour le serveur ':'Sites discovered for server ',
 'Sauvegarde demandée : ':'Backup requested: ',
 'Restauration isolée demandée : ':'Isolated restore requested: ',
 'Archive chiffrée téléchargée : ':'Encrypted archive downloaded: ',
 'Export ZIP déchiffré téléchargé : ':'Decrypted ZIP export downloaded: ',
 'Échec tâche ':'Task failed: ',
 'Dossier inaccessible : ':'Folder inaccessible: ',
 'Vhost inaccessible : ':'Virtual host inaccessible: ',
}
def translate(message):
    if request.headers.get('X-BackupCenter-Language','fr')!='en' or not isinstance(message,str): return message
    if message in MESSAGES:return MESSAGES[message]
    for prefix,value in PREFIXES.items():
        if message.startswith(prefix):return value+message[len(prefix):]
    import re
    match=re.fullmatch(r'Tâche (.+) terminée',message)
    if match:return 'Task '+match[1]+' completed'
    match=re.fullmatch(r'(\d+) site\(s\) ajouté\(s\) depuis la détection du serveur (.+)',message)
    if match:return match[1]+' site(s) added from server discovery '+match[2]
    match=re.fullmatch(r'Fichiers extraits dans (.+)/files ; exports SQL disponibles à côté. Aucun import MySQL et aucun remplacement du site actif.',message)
    if match:return 'Files extracted to '+match[1]+'/files; SQL dumps are alongside them. No MySQL import or replacement of the live site.'
    suffix=' Archive expirée selon la rétention.'
    if message.endswith(suffix):return translate(message[:-len(suffix)])+' Archive expired under the retention policy.'
    return message
