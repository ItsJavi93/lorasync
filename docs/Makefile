TEXFILE := fundamentos-lora
LATEXMK := latexmk
LATEXMKFLAGS := -pdf -interaction=nonstopmode -halt-on-error

.PHONY: all clean cleanall

all: $(TEXFILE).pdf

$(TEXFILE).pdf: $(TEXFILE).tex secciones/*.tex referencias.bib
	$(LATEXMK) $(LATEXMKFLAGS) $(TEXFILE).tex

clean:
	$(LATEXMK) -c $(TEXFILE).tex

cleanall:
	$(LATEXMK) -C $(TEXFILE).tex
