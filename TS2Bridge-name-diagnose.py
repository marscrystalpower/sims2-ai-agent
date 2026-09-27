"""Diagnose one neighborhood NID without changing the neighborhood or characters."""
import argparse
import importlib.util
import json
from pathlib import Path


def load_probe():
    path = Path(__file__).resolve().with_name('TS2Bridge-name-probe.py')
    spec = importlib.util.spec_from_file_location('ts2_name_probe',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def diagnose(folder, nid, map_path=None):
    probe=load_probe()
    hoods=list(folder.glob('*_Neighborhood.package'))
    if len(hoods)!=1 or not (folder/'Characters').is_dir():
        raise ValueError('expected a neighborhood folder with one *_Neighborhood.package and Characters directory')
    record=next((s for s in probe.neighborhood(hoods[0].read_bytes()) if s['nid']==nid),None)
    if record is None:
        return {'neighborhood':folder.name,'nid':nid,'status':'nid_absent_from_neighborhood'}
    matches=[];same_name=[];errors=0;scanned=0
    for p in sorted((folder/'Characters').glob('*.package')):
        scanned+=1
        try:
            identity=probe.character(p.read_bytes())
        except (ValueError,IndexError):
            errors+=1
            continue
        item={'file':p.name,'simGuid':identity['simGuid'],'firstName':identity['firstName'],
              'lastName':identity['lastName'],'description':identity['description']}
        if identity['simGuid']==record['simGuid']:
            matches.append(item)
        elif identity['firstName'].casefold()=='cooper' and identity['lastName'].casefold()=='corsillo':
            same_name.append(item)
    mapped=None
    if map_path:
        mapping=json.loads(map_path.read_text(encoding='utf-8'))
        if mapping.get('neighborhood')!=folder.name:
            raise ValueError('map neighborhood does not match folder')
        mapped=next((x for x in mapping.get('resolved',[]) if x.get('nid')==nid),None)
    return {'neighborhood':folder.name,'nid':nid,'neighborhoodGuid':record['simGuid'],
            'mapEntry':mapped,'matchingCharacterPackages':matches,
            'sameNameDifferentGuid':same_name,'characterPackagesScanned':scanned,
            'characterPackagesWithUnsupportedIdentity':errors,
            'status':'matched' if matches else 'no_matching_character_guid'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('neighborhood',type=Path)
    p.add_argument('--nid',type=int,required=True)
    p.add_argument('--names',type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if not 1 <= a.nid <= 65535:p.error('NID must be 1..65535')
    try:
        result=diagnose(a.neighborhood,a.nid,a.names)
    except (OSError,ValueError) as e:p.error(str(e))
    payload=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if a.output:a.output.write_text(payload,encoding='utf-8')
    else:print(payload,end='')

if __name__=='__main__':main()
