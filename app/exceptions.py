class DrugNotFoundException(Exception):
    error_code = "DRUG_NOT_FOUND"
    status_code = 404

    def __init__(self, drug_id: str):
        self.message = f"Drug not found: {drug_id}"
        super().__init__(self.message)


class NoFormulationException(Exception):
    error_code = "NO_FORMULATION"
    status_code = 404

    def __init__(self, drug_id: str):
        self.message = f"No formulation found for drug: {drug_id}"
        super().__init__(self.message)


class NoLabelDataException(Exception):
    error_code = "NO_LABEL_DATA"
    status_code = 404

    def __init__(self, master_linkage_id: str):
        self.message = f"No label data found for master_linkage_id: {master_linkage_id}"
        super().__init__(self.message)


class NoDosingDataException(Exception):
    error_code = "NO_DOSING_DATA"
    status_code = 404

    def __init__(self, drug_id: str):
        self.message = f"No dosing data found for drug: {drug_id}"
        super().__init__(self.message)


class DatabaseException(Exception):
    error_code = "DB_ERROR"
    status_code = 500

    def __init__(self, message: str = "Database error"):
        self.message = message
        super().__init__(self.message)


class CacheException(Exception):
    error_code = "CACHE_ERROR"
    status_code = 500

    def __init__(self, message: str = "Cache error"):
        self.message = message
        super().__init__(self.message)
