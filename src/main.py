from insurance_agent.ingest.ingest_to_db import ingest_adjuster_reports
from insurance_agent.ingest.adjuster_report import parse_adjuster_report
if __name__ == "__main__":

    path = "/code/insurance-agent/data/strata-insurance-corpus/docs/claim"
    adjuster_reports = parse_adjuster_report(path)

    #for adjuster_report in adjuster_reports:

        #print(adjuster_report.fields)
        #print(adjuster_report.sections['Findings'])
        #print(adjuster_report.raw_markdown)
    ingest_adjuster_reports(adjuster_reports)
