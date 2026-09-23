"""Acces PUBLIC a Backpack, et economie du fill passif sur un perpetuel.

Pourquoi ce paquet existe : la seule famille du projet dont le net mesure ait
jamais ete positif est la SUBVENTION — etre paye pour coter. Elle a ete
mesuree sur Polymarket, venue exclue du cadre (juridiction France). Backpack
publie un programme de market making equivalent et opere une entite EEA
(Trek Labs Europe Ltd, CySEC 273/15). Ce paquet y transporte la mesure.

Ce qui change par rapport a Polymarket, et qui est LE point dur : sur un
binaire, YES + NO = 1,00 par construction, donc neutraliser un inventaire est
sans risque et son prix se LIT. Sur un perpetuel, un fill subi laisse une
position directionnelle dont le cout de sortie est une DERIVE a mesurer.
C'est le markout, et c'est exactement ce qui a ferme la famille maker OKX.
"""
