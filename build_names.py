"""The community (category, index) -> name list, used by gen_item_names.py to fill gaps.

Run on its own it writes item_names.json from this list only, and only when there's no fuller file
already (the watcher and gen_item_names.py build the full list from your own game)."""
import json, os
from chest_watch import BASE

RAW = """
000D 0000 High Potion|000D 0024 High Mind Up|0007 0001 Summon Spirit|0006 0001 Gergoth Soul
0003 0039 Blue Knuckles|000D 0022 Super Potion|000D 0025 Mana Prism|0004 001D Combo Boots
0004 0076 Hercules Ring|0004 00BC Spiked Mail|0003 003C Mjollnir|0007 0000 Dark Metamorphosis
0005 0005 Bible|0006 0004 Puppet Master Soul|0004 0068 Thief Ring|0004 00A6 Healing Mail
0003 0006 Muramasa|0003 0085 Lapiste/Pneuma|0007 0002 Hellfire|0005 000A Wrecking Ball
0003 0078 Heaven's Sword|0004 0050 Chaos Ring|0003 0048 Nebula|0003 0086 Lapiste/Pneuma
0007 0003 Tetra Spirit|0005 0002 Holy Water|0006 0005 Brauner Soul|0003 0055 Daybreak
0004 0065 Gold Ring|0004 0096 Olrox's Tunic|000D 002B Caviar|000D 0005 Remedy
0004 00A5 Feather Gear|0003 0010 Alucart Sword|0003 007C Gungnir|000D 003D Ramen
0007 0004 Soul Steal|0005 0003 Cross|000C 0003 Guardian Knuckle|000C 0007 Omnia Vanitas
0006 0002 Death Soul|0003 0030 Milican's Sword|0003 000A Masamune|000D 002C Truffle
0003 0043 Throwing Sickle|0004 00A9 Ancient Armor|0004 0085 Death's Robe|0004 00A4 Berserker Mail
0003 0000 Death's Scythe|000D 003C Beef Curry|0006 0003 Dracula Soul|000C 0005 Sonic Strike
000D 002D Foie Gras|0003 0011 Valmanway|0003 004E Claimh Solais|0004 0001 Winged Boots
0004 00CC Minerva Mail|0004 00CB Simon's Plate|0004 00A7 Impervious Mail|0004 0086 Dracula's Tunic
0006 0000 Astarte Soul|0004 0007 Mercury Boots|0004 0023 Royal Crown|0004 0067 Miser Ring
0004 00A8 Robe Decolette|0003 0013 Badelaire|0006 0006 Legion Soul|0004 00E9 Simon's Cloak
0005 000C Javelin|0009 0005 Globus|0004 00E7 Simon's Cloak|0004 00EE Daikoku Raiment
000D 003E Philosopher's Stone|0009 0006 Acerbatus|0006 003B Beelzebub Soul|0004 00ED Sonic Boots
0003 0088 Kaiser Knuckle +1|0003 0087 Alucard Sword +1|0003 0089 Dextro Custos|0003 008A Sinestro Custos
0005 000D R. Boomerang|0005 000E R. Firebomb|0006 004F R. The Count Soul|0004 00F3 Retro Cloak
0004 00F0 Retro Armor|0004 00F1 Retro Helm|0004 00F2 Retro Greaves|0004 00F4 Feather Boots
0003 008B Valmanway +1|0003 008C Claimh Solais +1|0009 0007 Luminatio|0006 0057 Ryukotsuki Soul
0004 00F8 Ebisu Raiment|0004 00F5 Fuma's Armor|0004 00F6 Fuma's Helm|0004 00F7 Fuma's Greaves
0003 008D Yasutsuna +1|0003 008E Muramasa +1
0006 0049 R. Zombie Soul|0006 0050 Kung Fugu Soul|0006 0051 Hellmont Soul|0006 0052 Demon Flower Soul
0006 0053 Pejora's Mask Soul|0006 0054 Peepillary Soul|0006 0055 Gyropyre Soul|0006 0056 Decapiclops Soul
0006 0058 Oxicutioner Soul|0006 0059 Chimyriad Soul
"""

def main():
    names = {}
    for entry in RAW.replace('\n', '|').split('|'):
        entry = entry.strip()
        if entry:
            cat, idx, name = entry.split(' ', 2)
            names[f'0x{BASE[int(cat, 16)] + int(idx, 16):X}'] = name
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'item_names.json')
    try:
        with open(path, encoding='utf-8') as fh:
            have = len(json.load(fh))
    except (OSError, ValueError):
        have = 0
    if have > len(names):
        raise SystemExit(f'item_names.json already has {have} names (built from your game); left alone. '
                         'Run gen_item_names.py in a stage to refresh it.')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(names, fh, indent=1, sort_keys=True)
    print(len(names), 'names; 0x46 =', names['0x46'])


if __name__ == '__main__':
    main()
