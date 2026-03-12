import os

from suql import suql_execute

CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))


def suql_runner(query, **kwargs):
    results, column_names, _ = suql_execute(
        query,
        {
            "insurance_leads": "lead_id",
            "insurance_quotes": "quote_id",
            "insurance_underwriting": "underwriting_id",
            "insurance_appointments": "appointment_id",
            "insurance_call_log": "call_id",
        },
        os.getenv("INSURANCE_DB_NAME", "insurance"),
        embedding_server_address="http://127.0.0.1:8509",
        source_file_mapping={
            "insurance_general_info": os.path.join(
                CURRENT_DIR, "insurance_general_info.txt"
            )
        },
    )

    if column_names:
        final_res = []
        for res in results:
            if isinstance(res, dict):
                final_res.append(res)
            else:
                final_res.append(
                    dict(zip(column_names, res))
                )
        return final_res
    return results
