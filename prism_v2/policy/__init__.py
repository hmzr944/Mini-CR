"""POLITIQUE ECONOMIQUE ADAPTATIVE.

Ce paquet ne contient aucune strategie. Il contient la boucle que le mandat
impose :

    observations -> etat -> valeur conditionnelle d'une action -> action
    -> execution -> PnL net -> retour -> mise a jour

Ce qui est fixe ici, ce sont les REGLES DE COMPTABILITE : ce qu'une decision
doit contenir pour etre auditable, ce qu'une action coute reellement, et
comment le capital immobilise est compte. Ce qui n'est PAS fixe, c'est la
representation de l'etat, le choix des variables et la politique elle-meme :
ils sont appris sur les donnees, jamais ecrits en dur.

NO_TRADE est une action de plein droit et sa valeur vaut exactement zero.
Une action n'est choisie que si sa valeur estimee DEPASSE zero apres tous
les couts. C'est la seule facon d'empecher le systeme de trader par defaut.
"""
