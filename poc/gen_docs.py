"""Génère des dossiers patients FICTIFS avec la vérité terrain (valeurs sensibles).

Sortie : poc/docs/NN.txt et poc/docs/NN.truth.json (liste de {valeur, type}).
Aucune donnée réelle : noms et numéros tirés au hasard (graine fixe).
"""
import json
import random
from pathlib import Path

random.seed(42)

PRENOMS = ["Marie", "Jean", "Odette", "Raymond", "Yvette", "Lucien", "Simone", "André",
           "Georgette", "Paulette", "Maurice", "Denise", "Henriette", "Fernand", "Lucette"]
NOMS = ["Dupont", "Lefèvre", "Moreau", "Garnier", "Boulanger", "Vasseur", "Delacroix",
        "Faure", "Marchand", "Lemaire", "Fontaine", "Carpentier", "Roussel", "Bertin"]
SOIGNANTS = ["Sophie Martin", "Karim Benali", "Céline Dubois", "Thomas Leroy", "Aïcha Diallo",
             "Nathalie Perrin", "Julien Masson"]
MEDECINS = ["Dr Pierre Aubry", "Dr Claire Fabre", "Dr Hugo Lambert"]
COMMUNES = ["Longpont-sur-Orge", "Montlhéry", "Linas", "Marcoussis", "Saint-Michel-sur-Orge",
            "Villemoisson", "Sainte-Geneviève-des-Bois"]
RUES = ["rue des Lilas", "avenue de la République", "impasse du Moulin", "rue Jean Jaurès",
        "chemin des Vignes", "place de l'Église"]
PATHOS = ["diabète de type 2", "insuffisance cardiaque", "maladie de Parkinson",
          "BPCO", "démence de type Alzheimer", "séquelles d'AVC", "arthrose invalidante"]


def nir():
    sexe = random.choice("12")
    return f"{sexe} {random.randint(30, 50):02d} {random.randint(1, 12):02d} 91 {random.randint(100, 600)} {random.randint(100, 999)} {random.randint(10, 97)}"


def tel():
    return "06 " + " ".join(f"{random.randint(0, 99):02d}" for _ in range(4))


def date():
    return f"{random.randint(1, 28):02d}/{random.randint(1, 12):02d}/{random.randint(1935, 1945)}"


def patient():
    p, n = random.choice(PRENOMS), random.choice(NOMS)
    return {
        "prenom": p, "nom": n, "full": f"{p} {n}",
        "nir": nir(), "naissance": date(), "tel": tel(),
        "adresse": f"{random.randint(1, 60)} {random.choice(RUES)}",
        "cp_ville": f"91{random.randint(100, 999)} {random.choice(COMMUNES)}",
        "dossier": f"SSIAD-{random.randint(2018, 2026)}-{random.randint(1000, 9999)}",
        "soignant": random.choice(SOIGNANTS), "medecin": random.choice(MEDECINS),
        "patho": random.choice(PATHOS),
        "aidant_nom": f"{random.choice(PRENOMS)} {n}",
        "aidant_tel": tel(),
        "mail": f"{p.lower()}.{n.lower()}@example.fr",
    }


def doc_bilan(p):
    return ("SSIAD - BILAN D'ÉVALUATION À L'ADMISSION\n"
            f"Dossier n° {p['dossier']}\n\n"
            f"Patient : Madame/Monsieur {p['nom'].upper()} {p['prenom']}, né(e) le {p['naissance']}\n"
            f"N° de sécurité sociale : {p['nir']}\n"
            f"Adresse : {p['adresse']}, {p['cp_ville']}\n"
            f"Téléphone : {p['tel']}\n\n"
            f"Médecin traitant : {p['medecin']}. Pathologie principale : {p['patho']}.\n"
            f"L'évaluation a été réalisée à domicile par {p['soignant']}, infirmière coordinatrice.\n"
            f"La personne vit seule ; sa fille {p['aidant_nom']} (tél. {p['aidant_tel']}) passe chaque week-end.\n"
            f"Autonomie : GIR 3. Toilette complète 5 jours sur 7, passage de {p['soignant']} à 8h30.\n")


def doc_cr(p):
    return ("COMPTE RENDU DE VISITE\n\n"
            f"Date : {random.randint(1, 28):02d}/10/2026\n"
            f"Intervenant(e) : {p['soignant']}\n\n"
            f"Chez {p['prenom']} {p['nom']} ce matin à {p['cp_ville'].split(' ', 1)[1]}. "
            f"M./Mme {p['nom']} se plaint de douleurs, lié(e) à son {p['patho']}. "
            f"J'ai appelé le {p['medecin']} qui a modifié le traitement. "
            f"Sa fille {p['aidant_nom']} a été prévenue par téléphone au {p['aidant_tel']}. "
            f"Rappeler {p['prenom']} au {p['tel']} demain. Courriel de la famille : {p['mail']}.\n")


def doc_courrier(p):
    return (f"{p['medecin']}\nCabinet médical, {random.choice(COMMUNES)}\n\n"
            f"Objet : demande de renouvellement de prise en charge - {p['full']}\n\n"
            f"Madame, Monsieur,\n\n"
            f"Je vous écris au sujet de mon patient {p['nom']} {p['prenom']} (né le {p['naissance']}, "
            f"NIR {p['nir']}), domicilié {p['adresse']} à {p['cp_ville']}. "
            f"Son état ({p['patho']}) justifie la poursuite des soins infirmiers quotidiens "
            f"assurés par {p['soignant']}. Dossier de référence {p['dossier']}.\n\n"
            f"Cordialement,\n{p['medecin']}\n")


def truth(p, text):
    """Valeurs sensibles effectivement présentes dans le texte."""
    cand = [
        (p["full"], "personne"), (p["nom"], "personne"), (p["prenom"], "personne"),
        (p["nir"], "nir"), (p["naissance"], "date_naissance"), (p["tel"], "telephone"),
        (p["aidant_tel"], "telephone"), (p["adresse"], "adresse"),
        (p["cp_ville"], "adresse"), (p["dossier"], "dossier"),
        (p["soignant"], "personne"), (p["medecin"].replace("Dr ", ""), "personne"),
        (p["aidant_nom"], "personne"), (p["mail"], "email"),
    ]
    return [{"valeur": v, "type": t} for v, t in cand if v in text]


def main():
    out = Path(__file__).parent / "docs"
    out.mkdir(exist_ok=True)
    n = 0
    for _ in range(5):
        p = patient()
        for fn in (doc_bilan, doc_cr, doc_courrier):
            n += 1
            text = fn(p)
            (out / f"{n:02d}.txt").write_text(text, encoding="utf-8")
            (out / f"{n:02d}.truth.json").write_text(
                json.dumps(truth(p, text), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{n} documents générés dans {out}")


if __name__ == "__main__":
    main()
