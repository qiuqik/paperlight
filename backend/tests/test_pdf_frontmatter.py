import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.model import Block, DocumentModel, Metadata, Section
from backend.app.normalizer import finalize_document_model
from backend.app.pdf_frontmatter import _names, repair_pdf_frontmatter
from backend.app.pdf_fidelity import _inline_candidate, attach_pdf_figure_captions
from backend.app.vision import configured_provider


class PdfRepairTests(unittest.TestCase):
    def test_author_rosters_ignore_roles_and_institutions(self):
        self.assertEqual(_names('Juntong Chen, Graduate Student Member, IEEE, Qiaoyun Huang, and Chenhui Li'),
                         ['Juntong Chen', 'Qiaoyun Huang', 'Chenhui Li'])
        self.assertEqual(_names('Shifu Chen* Xiaodan Miao† Dazhen Deng‡'),
                         ['Shifu Chen', 'Xiaodan Miao', 'Dazhen Deng'])
        self.assertEqual(_names('Kaiming He\nXiangyu Zhang\nMicrosoft Research\n{kahe}@microsoft.com'),
                         ['Kaiming He', 'Xiangyu Zhang'])
        self.assertEqual(_names('Alec Radford * 1 Jong Wook Kim * 1 Chris Hallacy 1 Aditya Ramesh 1'),
                         ['Alec Radford', 'Jong Wook Kim', 'Chris Hallacy', 'Aditya Ramesh'])
        self.assertEqual(_names('Vlad-Constantin Lungu-Stan, Ionut¸ Mironic˘a, Mariana-Iuliana Georgescu'),
                         ['Vlad-Constantin Lungu-Stan', 'Ionut Mironica', 'Mariana-Iuliana Georgescu'])
        self.assertEqual(_names('Alice Lee'), ['Alice Lee'])

    def test_first_page_repairs_author_and_moves_email(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sample.pdf'
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((60, 80), 'Example Paper')
            page.insert_text((60, 110), 'Alice Lee, Bob Chen, and Carol Wu')
            page.insert_text((60, 170), 'Abstract')
            pdf.save(path)
            pdf.close()
            model = DocumentModel(id='sample', metadata=Metadata(title='Example Paper', authors=['IEEE']),
                                  sections=[Section(id='abstract', title='Abstract', type='abstract', blocks=[
                                      Block(id='email', type='footnote', page=1, text='e-mail: alice@example.org'),
                                      Block(id='body', type='paragraph', page=1, text='The paper is about geometry.')])])
            self.assertEqual(repair_pdf_frontmatter(model, path), 2)
            self.assertEqual(model.metadata.authors, ['Alice Lee', 'Bob Chen', 'Carol Wu'])
            self.assertEqual([block.id for block in model.sections[0].blocks], ['body'])

    def test_numbered_pdf_caption_attaches_to_existing_visual(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sample.pdf'
            pdf = pymupdf.open()
            page = pdf.new_page(width=600, height=800)
            page.insert_text((315, 312), 'Fig. 5. Kernel bandwidth and class maps.')
            pdf.save(path)
            pdf.close()
            figure = Block(id='figure-auto-1', type='figure', page=1, label='Illustration',
                           bbox={'x': 314, 'y': 60, 'width': 230, 'height': 235})
            model = DocumentModel(id='sample', metadata=Metadata(),
                                  sections=[Section(id='body', title='Body', blocks=[figure])], figures=[figure])
            self.assertEqual(attach_pdf_figure_captions(model, path), 1)
            self.assertEqual(model.figures[0].label, 'Figure 5')
            self.assertEqual(model.sections[0].blocks[0].id, 'figure-5')

    def test_tiny_diagram_text_does_not_become_body(self):
        tiny = Block(id='diagram-label', type='paragraph', text='"unit": {', page=1,
                     bbox={'x': 20, 'y': 50, 'width': 40, 'height': 3.5})
        prose = Block(id='prose', type='paragraph', text='Readable prose.', page=1,
                      bbox={'x': 20, 'y': 100, 'width': 240, 'height': 12})
        model = DocumentModel(id='sample', metadata=Metadata(),
                              sections=[Section(id='body', title='Body', blocks=[tiny, prose])])
        self.assertEqual([block.id for block in finalize_document_model(model).sections[0].blocks], ['prose'])

    def test_inline_candidates_require_complete_expression(self):
        self.assertTrue(_inline_candidate('F(x) := H(x)−x'))
        self.assertFalse(_inline_candidate('P ='))
        self.assertFalse(_inline_candidate('O(nth2'))

    def test_server_side_aliases_select_image_capable_model(self):
        with patch.dict('os.environ', {'PAPERLIGHT_DEEPSEEK_API_KEY': '', 'DEEPSEEK_APIKEY': 'server-key',
                                      'Doubao_APIKEY': 'other-key', 'Doubao_Model_id': 'doubao-seed-2-0-lite-260428'}):
            provider = configured_provider()
        self.assertEqual(provider.name, 'deepseek')


if __name__ == '__main__':
    unittest.main()
