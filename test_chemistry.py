import unittest
from chemistry import attach, rating

class ChemistryTests(unittest.TestCase):
 def test_live_examples(self):
  for a,b,expected in [(0,8,1),(110,40,2),(108,73,3),(-42,74,-1)]:
   self.assertEqual(rating(a,b),expected)
   self.assertEqual(rating(b,a),expected)
 def test_formula_edges(self):
  for a,b,expected in [(-25,-25,-1),(-24,-24,0),(0,0,0),(1,1,1),(34,34,1),(35,35,2),(89,89,2),(90,90,3),(-25,75,1),(-26,76,-1),(-26,26,0)]:
   self.assertEqual(rating(a,b),expected)
 def test_conservative_pair_handling(self):
  def pair(a,b,score):return dict(viewerNid=a,otherNid=b,rawSlots=[0]*8+[score,0],known=True,hasFamilyTie=False)
  sims=[dict(nid=1,ageStage='adult'),dict(nid=2,ageStage='adult')]
  pairs=[pair(1,2,-42),pair(2,1,74)]
  attach(pairs,sims)
  self.assertEqual(pairs[0]['mutualChemistry'],pairs[1]['mutualChemistry'])
  self.assertEqual(pairs[0]['mutualChemistry']['category'],'poor')
  pairs[1]['rawSlots']=[0]*9+[74]
  attach(pairs,sims)
  self.assertTrue(all(p['mutualChemistry']['status']=='unknown' for p in pairs))
  pairs=[pair(1,2,0)]
  attach(pairs,sims)
  self.assertEqual(pairs[0]['mutualChemistry']['status'],'unknown')
  for field,value in [('known',False),('hasFamilyTie',True)]:
   pairs=[pair(1,2,20),pair(2,1,20)];pairs[1][field]=value;attach(pairs,sims)
   self.assertTrue(all(p['mutualChemistry']['status']=='unknown' for p in pairs))
  pairs=[pair(1,2,20),pair(2,1,20)]
  attach(pairs,[dict(nid=1,ageStage='teen'),sims[1]])
  self.assertTrue(all(p['mutualChemistry']['status']=='unknown' for p in pairs))
if __name__=='__main__':unittest.main()
