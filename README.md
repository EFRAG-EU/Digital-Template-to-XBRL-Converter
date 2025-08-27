# VSME Digital Template to XBRL Converter

This Excel-to-XBRL converter has been developed by EFRAG to support the SME ecosystem in preparing reports aligned with the [Voluntary Sustainability Reporting Standard for non-listed SMEs (VSME)](https://www.efrag.org/en/projects/voluntary-reporting-standard-for-smes-vsme/concluded).

The tool enables users to convert data from structured Excel templates into XBRL (eXtensible Business Reporting Language) format by leveraging Excel named ranges. Each named range in the Excel file must correspond exactly to the local name of the relevant element in the VSME XBRL taxonomy. This ensures accurate and standards-compliant digital tagging of sustainability disclosures.

> [!WARNING]  
> The Digital Template has been developed and tested with the Microsoft Excel desktop application. It might not work fully correct in other applications. When reporting issues, please indicate which application has been used to fill the Digital Template.

A free instance of this tool is running on the [EFRAG XBRL site](https://xbrl.efrag.org/convert/)

More information on the project, including an [Explanatory Note (PDF)](https://xbrl.efrag.org/downloads/vsme/VSME-Digital-Template-and-XBRL-Taxonomy-Explanatory-Note-May-2025.pdf) is provided on [EFRAG's website](https://www.efrag.org/en/vsme-digital-template-and-xbrl-taxonomy).

## Key Features

- Streamlines digital sustainability reporting.
- Maps Excel data to an XBRL instance using named ranges.
- Compatible with the VSME Digital Template and XBRL Taxonomy developed by EFRAG.
- Produces a human-readable Inline XBRL file (including viewer) based on the Presentation Linkbase of the XBRL taxonomy.
- Converts the Inline XBRL report to an XBRL Report Package and XBRL-JSON report.
- Comes with full certified XBRL validation powered by [Arelle](https://arelle.org/arelle/).
- Can be deployed as a web-server using Flask, or as a command-line tool.

## Requirements

- Python enviroment and dependencies (see below).
- Excel template with named ranges matching VSME taxonomy element names (see [digital templates](https://github.com/EFRAG-EU/Digital-Template-to-XBRL-Converter/tree/main/digital-templates)).
- XBRL taxonomy files for validation and mapping.

More information can be found on the [EFRAG webpage](https://www.efrag.org/en/sustainability-reporting/esrs-workstreams/digital-tagging-with-xbrl-taxonomies)

## Building and deploying the converter

### Initital setup

- Install Python 3.11 or later (available from [the Microsoft Store](https://apps.microsoft.com/detail/9nrwmjp3717k))
- Install `venv`
  - `pip install venv`
- Clone repository `git clone https://github.com/EFRAG-EU/Digital-Template-to-XBRL-Converter.git`
- Create a `.venv` inside the repository
  - `cd Digital-Template-to-XBRL-Converter`
  - `python -m venv .venv`
- `.venv/Scripts/activate` Always do this when you open a new terminal, before trying to run commands from the repository
  - `source .venv/bin/activate` on Linux
- `pip install .`

### Convert an Excel file to an inline XBRL file (command-line)

```bash
python ./scripts/parse-and-ixbrl.py example.xlsx  output.html
```

### Run webserver locally

```bash
python -m flask --app mireport.webapp run
```

## Developers

### Set-up for developing

Follow [the user instructions](#set-up-for-deploying) but make the distribution editable:

* `pip install -e ".[dev]"`

### Run auto-redeploying and debug webserver

```bash
python -m flask --app mireport.webapp run --debug
```

### Dump the named ranges from an Excel file (for debugging/testing purposes)

```bash
python .\scripts\parse-and-dump.py example.xlsx
```

## List of dependencies

- **[aoix](https://code.blinkace.com/xbrl/aoix.git)**: Custom fork for XBRL integration (feature-typed-dimensions branch).
- **[arelle-release](https://pypi.org/project/arelle-release/)**: Arelle XBRL software release.
- **[Flask-Session](https://pypi.org/project/Flask-Session/)**: Adds server-side session support to Flask applications.
- **[Flask](https://pypi.org/project/Flask/)**: A lightweight WSGI web application framework.
- **[ixbrl-viewer](https://pypi.org/project/ixbrl-viewer/)**: Inline XBRL viewer.
- **[msgpack](https://pypi.org/project/msgpack/)**: MessagePack (de)serializer.
- **[openpyxl](https://pypi.org/project/openpyxl/)**: Read/write Excel 2010 xlsx/xlsm/xltx/xltm files.
- **[pillow](https://python-pillow.github.io/)**: Image (PNG, JPEG) inspection and processing.
- **[pydantic](https://pypi.org/project/pydantic/)**: Data validation and settings management using Python type annotations.
- **[python-dateutil](https://pypi.org/project/python-dateutil/)**: Extensions to the standard Python datetime module.
- **[python-dotenv](https://pypi.org/project/python-dotenv/)**: Read key-value pairs from `.env` files and set them as environment variables.
- **[rich](https://pypi.org/project/rich/)**: Rich text and beautiful formatting in the terminal.
- **[waitress](https://pypi.org/project/waitress/)**: WSGI server for Python.

## Funding

<img src="https://www.efrag.org/sites/default/files/styles/pg_text_media/public/2023-12/166824540_max.jpg" width=20% height=20% alt="EU flag">

EFRAG is co-funded by the European Union through the Single Market Programme in which the EEA-EFTA countries (Norway, Iceland and Liechtenstein), as well as Kosovo participate. Any views and opinions expressed are however those of the EFRAG Secretariat only and do not necessarily reflect those of the European Union, the European Commission or of countries that participate in the Single Market Programme. Neither the European Union, the European Commission nor countries participating in the Single market Programmecan be held responsible for them.

## How to contribute

- Contribute to the project by raising issues in Github (provide all information to reproduce the issue)
- Become a [Friend of EFRAG](https://www.efrag.org/en/about-us/friends-of-efrag) to support us on our mission and to ensure continued maintenance.
- If you would like to become contributor, please clone the repository and provide well documented pull requests, including unit tests.
