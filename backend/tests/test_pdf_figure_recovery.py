"""Recover stacked vector charts while respecting the intervening caption."""
import tempfile
import unittest
from pathlib import Path

import pymupdf

from backend.app.model import DocumentModel, Metadata, Section, Block
from backend.app.pdf_fidelity import recover_missing_pdf_figures, repair_pdf_word_spacing, expand_pdf_figure_crops
from backend.app.pdf_pipeline import _needs_ocr
from types import SimpleNamespace


class FigureRecoveryTests(unittest.TestCase):
    def test_schema_caption_inside_code_block_keeps_its_source_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((60,80),'field: null\nFigure 6: Schema panel.')
            path = root/'source.pdf'
            pdf.save(path)
            pdf.close()
            code = Block(id='schema',type='code',text='field: null',page=1,src='/api/documents/p/assets/schema.png',
                         bbox={'x':60,'y':66,'width':200,'height':17})
            model = DocumentModel(id='p',metadata=Metadata(),sections=[Section(id='body',title='Body',blocks=[code])])
            self.assertEqual(recover_missing_pdf_figures(model,path,root,{'schema'}),0)
            self.assertEqual(recover_missing_pdf_figures(model,path,root),1)
            self.assertEqual((code.id,code.type,code.number),('schema','figure',6))
            self.assertEqual(code.caption,'Schema panel.')

    def test_multiple_panels_expand_without_publisher_title_or_neighbor_caption(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = pymupdf.open()
            page = pdf.new_page(width=600,height=800)
            page.insert_text((60,50),'A Publisher Title Before The Figure',fontsize=20)
            page.draw_rect(pymupdf.Rect(70,90,320,210),fill=(.2,.5,.7))
            page.draw_rect(pymupdf.Rect(100,230,280,330),fill=(.5,.7,.3))
            page.insert_text((312,343),'Figure 2: Neighbor.')
            page.insert_text((60,370),'Figure 1: Two panels.')
            path = root/'source.pdf'
            pdf.save(path)
            pdf.close()
            figure = Block(id='figure-1',type='figure',number=1,page=1,caption='Two panels.',
                           bbox={'x':100,'y':230,'width':180,'height':100})
            model = DocumentModel(id='p',metadata=Metadata(),sections=[Section(id='s',title='Body',blocks=[figure])],figures=[figure])
            self.assertEqual(expand_pdf_figure_crops(model,path,root,{'figure-1'}),0)
            self.assertEqual(expand_pdf_figure_crops(model,path,root),1)
            self.assertGreater(figure.bbox['y'],70)
            self.assertLess(figure.bbox['y'],100)
            self.assertGreater(figure.bbox['height'],230)
            self.assertLess(figure.bbox['width'],270)
            with Image.open(root/'assets'/'expanded_figure-1.png') as image:
                self.assertGreater(image.height,500)

    def test_vector_supplement_with_text_layer_does_not_trigger_ocr(self):
        item = SimpleNamespace(text='body '*100,prov=[SimpleNamespace(page_no=1)])
        doc = SimpleNamespace(pages={1:None,2:None},iterate_items=lambda:iter([(item,0)]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'source.pdf'
            pdf = pymupdf.open()
            pdf.new_page().insert_text((50,80),'A normal digital article with a selectable text layer.')
            pdf.new_page().insert_text((50,80),'Figure 1: A vector supplement with selectable labels.')
            pdf.save(path)
            pdf.close()
            self.assertTrue(_needs_ocr(doc))
            self.assertFalse(_needs_ocr(doc,path))
            pdf = pymupdf.open(path)
            pdf[1].add_redact_annot(pdf[1].rect)
            pdf[1].apply_redactions()
            scanned = Path(directory)/'scanned.pdf'
            pdf.save(scanned)
            pdf.close()
            self.assertTrue(_needs_ocr(doc,scanned))

    def test_line_hyphen_spacing_restored_only_without_protected_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'source.pdf'
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((50,80),'This paragraph describes cartog-\nraphy and the comparison of different methods.')
            rect = page.get_text('blocks')[0][:4]
            pdf.save(path)
            pdf.close()
            text = 'This paragraph describes cartog raphy and the comparison of different methods.'
            block = Block(id='p',type='paragraph',text=text,page=1,
                          bbox={'x':rect[0],'y':rect[1],'width':rect[2]-rect[0],'height':rect[3]-rect[1]})
            model = DocumentModel(id='paper',metadata=Metadata(),sections=[Section(id='body',title='Body',blocks=[block])])
            self.assertEqual(repair_pdf_word_spacing(model,path,{'p'}),0)
            self.assertEqual(block.text,text)
            self.assertEqual(repair_pdf_word_spacing(model,path),1)
            self.assertIn('cartography',block.text)

    def test_lower_page_vector_chart_does_not_include_previous_figure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = pymupdf.open()
            page = pdf.new_page(width=600,height=800)
            page.draw_rect(pymupdf.Rect(320,280,550,365),fill=(.2,.5,.7))
            page.insert_text((315,380),'Figure 1: Previous chart.')
            page.draw_rect(pymupdf.Rect(330,410,535,500),fill=(.5,.7,.3))
            page.insert_text((315,520),'Figure 2: Lower chart.')
            source = root/'original.pdf'
            pdf.save(source)
            pdf.close()
            existing = Block(id='figure-1',type='figure',number=1,caption='Previous chart',page=1)
            paragraph = Block(id='p',type='paragraph',page=1,text='Body',bbox={'x':315,'y':540,'width':240,'height':50})
            model = DocumentModel(id='paper',metadata=Metadata(),
                                  sections=[Section(id='body',title='Body',blocks=[existing,paragraph])],figures=[existing])
            self.assertEqual(recover_missing_pdf_figures(model,source,root),1)
            figure = next(b for b in model.figures if b.number==2)
            self.assertGreaterEqual(figure.bbox['y'],400)
            self.assertLessEqual(figure.bbox['height'],100)
            self.assertTrue((root/'assets'/'recovered_figure_2.png').is_file())
            self.assertEqual(recover_missing_pdf_figures(model,source,root),0)


if __name__ == '__main__':
    unittest.main()
