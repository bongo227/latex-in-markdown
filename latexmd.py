"""
Tweaked version of "Python-Markdown LaTeX Extension".
"""

import re
import os
import string
import base64
import tempfile
import markdown
import hashlib

from subprocess import call, PIPE


# Defines our basic inline image
IMG_EXPR = "<div class='latex-box math-false'><img class='' alt='%s' id='%s'" + \
        " src='data:image/png;base64,%s'></div>"

INLINE_IMG_EXPR = "<img class='math-true' alt='%s' id='%s'" + \
        " src='data:image/png;base64,%s'>"

# Base CSS template
IMG_CSS = """
<style scoped>
.latex-box.math-false {text-align: center;}
.math-true {vertical-align: middle;}
</style>
"""


class LaTeXPreprocessor(markdown.preprocessors.Preprocessor):
    # These are our cached expressions that are stored in latex.cache
    cached = {}

    # Basic LaTex Setup as well as our list of expressions to parse
    tex_preamble = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{amsthm}
\usepackage{amssymb}
\usepackage{bm}
\usepackage{parskip}
\usepackage[usenames,dvipsnames]{color}
\pagestyle{empty}
"""

    def __init__(self, configs):
        try:
            cache_file = open('latex.cache', 'r+')
            for line in cache_file.readlines():
                key, val = line.strip("\n").split(" ")
                self.cached[key] = val
        except Exception:
            pass

        self.config = {}
        self.config[("general", "preamble")] = ""
        self.config[("dvipng", "args")] = "-q -T tight -bg Transparent -z 9 -D 106"
        self.config[("delimiters", "text")] = "%"
        self.config[("delimiters", "math")] = "£"
        self.config[("delimiters", "preamble")] = "%%"

        def build_regexp(delim):
            delim = re.escape(delim)
            regexp = r'(?<!\\)' + delim + r'(.+?)(?<!\\)' + delim
            return re.compile(regexp, re.MULTILINE | re.DOTALL)

        # %TEXT% mode which is the default LaTeX mode.
        self.re_textmode = build_regexp(self.config[("delimiters", "text")])
        # $MATH$ mode which is the typical LaTeX math mode.
        self.re_mathmode = build_regexp(self.config[("delimiters", "math")])
        # %%PREAMBLE%% text that modifys the LaTeX preamble for the document
        self.re_preamblemode = build_regexp(self.config[("delimiters", "preamble")])

    """The TeX preprocessor has to run prior to all the actual processing
    and can not be parsed in block mode very sanely."""
    def _latex_to_base64(self, tex, math_mode):
        """Generates a base64 representation of TeX string"""
        # Generate the temporary file
        tempfile.tempdir = ""
        tmp_file_fd, path = tempfile.mkstemp()
        tmp_file = os.fdopen(tmp_file_fd, "w")
        tmp_file.write(self.tex_preamble)

        # Figure out the mode that we're in
        if math_mode:
            tmp_file.write("$%s$" % tex)
        else:
            tmp_file.write("%s" % tex)

        tmp_file.write('\n\end{document}')
        tmp_file.close()

        # compile LaTeX document. A DVI file is created
        status = call(('latex -halt-on-error %s' % path).split(), stdout=PIPE)

        # clean up if the above failed
        if status:
            self._cleanup(path, err=True)
            raise Exception("Couldn't compile LaTeX document." +
                "Please read '%s.log' for more detail." % path)

        # Run dvipng on the generated DVI file. Use tight bounding box.
        # Magnification is set to 1200
        dvi = "%s.dvi" % path
        png = "%s.png" % path

        # Extract the image
        cmd = "dvipng %s %s -o %s" % (self.config[("dvipng", "args")], dvi, png)
        status = call(cmd.split(), stdout=PIPE)

        # clean up if we couldn't make the above work
        if status:
            self._cleanup(path, err=True)
            raise Exception("Couldn't convert LaTeX to image." +
                    "Please read '%s.log' for more detail." % path)

        # Read the png and encode the data
        png = open(png, "rb")
        data = png.read()
        data = base64.b64encode(data)
        png.close()

        self._cleanup(path)

        return str(data)[2:-1]

    def _cleanup(self, path, err=False):
        # don't clean up the log if there's an error
        extensions = ["", ".aux", ".dvi", ".png", ".log"]
        if err:
            extensions.pop()

        # now do the actual cleanup, passing on non-existent files
        for extension in extensions:
            try:
                os.remove("%s%s" % (path, extension))
            except (IOError, OSError):
                pass

    def run(self, lines):
        """Parses the actual page"""
        # Re-creates the entire page so we can parse in a multine env.
        page = "\n".join(lines)

        # Adds a preamble mode
        self.tex_preamble += self.config[("general", "preamble")]
        preambles = self.re_preamblemode.findall(page)
        for preamble in preambles:
            self.tex_preamble += preamble + "\n"
            page = self.re_preamblemode.sub("", page, 1)
        self.tex_preamble += "\n\\begin{document}\n"

        # Figure out our text strings and math-mode strings
        tex_expr = [(self.re_textmode, False, x) for x in self.re_textmode.findall(page)]
        tex_expr += [(self.re_mathmode, True, x) for x in self.re_mathmode.findall(page)]

        # No sense in doing the extra work
        if not len(tex_expr):
            return page.split("\n")

        # Parse the expressions
        new_cache = {}
        id = 0
        for reg, math_mode, expr in tex_expr:
            simp_expr = hashlib.md5(expr.encode('utf-8')).hexdigest()
            print(simp_expr)
            exp = ""
            if simp_expr in self.cached:
                data = self.cached[simp_expr]
            else:
                try:
                    data = self._latex_to_base64(expr, math_mode)
                    new_cache[simp_expr] = data
                except Exception as e:
                    exp = e
                    data = ""
            expr = expr.replace('"', "").replace("'", "")
            id += 1
            if data:
                if math_mode:
                    page = reg.sub(INLINE_IMG_EXPR %
                        (simp_expr, simp_expr + "_" + str(id), data), page, 1)
                else:
                    page = reg.sub(IMG_EXPR %
                        (simp_expr, simp_expr + "_" + str(id), data), page, 1)
            else:
                # TODO: create a model to show logs
                exp = "ERROR"
                page = reg.sub("<p>{}</p>".format(exp), page, 1)

        # Perform the escaping of delimiters and the backslash per se
        tokens = []
        tokens += [self.config[("delimiters", "preamble")]]
        tokens += [self.config[("delimiters", "text")]]
        tokens += [self.config[("delimiters", "math")]]
        tokens += ['\\']
        for tok in tokens:
            page = page.replace('\\' + tok, tok)

        # Cache our data
        cache_file = open('latex.cache', 'a')
        for key, value in new_cache.items():
            cache_file.write("%s %s\n" % (key, value))
        cache_file.close()

        # Make sure to resplit the lines
        return page.split("\n")


class LaTeXPostprocessor(markdown.postprocessors.Postprocessor):
        """This post processor extension just allows us to further
        refine, if necessary, the document after it has been parsed."""
        def run(self, text):
            # Inline a style for default behavior
            text = IMG_CSS + text
            return text


class MarkdownLatex(markdown.Extension):
    """Wrapper for LaTeXPreprocessor"""
    def extendMarkdown(self, md, md_globals):
        # Our base LaTeX extension
        md.preprocessors.add('latex',
                LaTeXPreprocessor(self), ">html_block")
        # Our cleanup postprocessing extension
        md.postprocessors.add('latex',
                LaTeXPostprocessor(self), ">amp_substitute")


def makeExtension(*args, **kwargs):
    """Wrapper for a MarkDown extension"""
    return MarkdownLatex(*args, **kwargs)
