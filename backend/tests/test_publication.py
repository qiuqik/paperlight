import json
import unittest
from backend.app.publication import parse_lookup, PublicationInfo

class PublicationTests(unittest.TestCase):
    title = 'A Reliable Paper Title'
    def response(self, **changes):
        value = dict(matched_title=self.title, publication_status='published', venue='Conference', publish_time=20260823, authors=['Alice'], publication_source_url='https://publisher.example/paper#1')
        value.update(changes)
        return {'content': [{'type':'web_search_tool_result','content':[{'type':'web_search_result','title':self.title,'url':'https://publisher.example/paper'}]}, {'type':'text','text':json.dumps(value)}]}
    def test_evidence_and_date(self):
        info,_ = parse_lookup(self.response(),self.title)
        self.assertEqual(info.publish_time,20260823)
        self.assertEqual(info.publication_source_url,'https://publisher.example/paper')
    def test_invented_source_downgrades(self):
        info,_ = parse_lookup(self.response(publication_source_url='https://invented.example/paper'),self.title)
        self.assertEqual(info.publication_status,'unknown')
        self.assertIsNone(info.venue)
        self.assertIsNone(info.publish_time)
    def test_wrong_paper_and_missing_search_rejected(self):
        with self.assertRaises(ValueError): parse_lookup(self.response(matched_title='Other work'),self.title)
        with self.assertRaises(ValueError): parse_lookup({'content':[]},self.title)
    def test_invalid_date_rejected(self):
        with self.assertRaises(ValueError): PublicationInfo(publish_time=20260230)
