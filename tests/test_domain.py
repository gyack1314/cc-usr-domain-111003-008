import unittest
from workbench.domain import *

class DomainBoundaryTests(unittest.TestCase):
    def test_boundaries(self):
        with self.assertRaises(ValueError):dry(1,1)
        with self.assertRaises(ValueError):censored('ND')
        self.assertEqual(censored(4),{'low':4.0,'high':4.0,'high_inclusive':True})
        events=[{'order':2,'sender':'x','receiver':'c','signed':True},{'order':1,'sender':'a','receiver':'b','signed':True}]
        self.assertEqual(custody(events,'a'),[2])
