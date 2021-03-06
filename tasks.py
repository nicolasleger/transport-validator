import requests, tempfile, os, transitfeed, pymongo, datetime
from io import BytesIO
from transport_validator.validator import Accumulator
from celery import Celery, states

app = Celery('tasks')
app.config_from_object('celeryconfig')

@app.task(bind=True)
def perform(self, url):
#Download the file
    r = requests.get(url)
    self.update_state(state='DOWNLOADED')
    if r.status_code != 200:
        raise Exception("Bad status code {} {}".format(url, r.status_code))

#Save it in a temp file
    d = tempfile.mkdtemp()
    filename = os.path.join(d, "gtfs.zip")
    with open(filename, "wb") as f:
        f.write(r.content)

#Load it
    accumulator = Accumulator()
    problems = transitfeed.ProblemReporter(accumulator)
    gtfs_factory = transitfeed.GetGtfsFactory()
    loader = gtfs_factory.Loader(filename, problems=problems,
                                 extra_validation=False,
                                 gtfs_factory=gtfs_factory)
    schedule = loader.Load() #this is a transport schedule
    self.update_state(state='LOADED')
#Remove tempfiles
    os.remove(filename)
    os.rmdir(d)

#Run validation & return results
    schedule.Validate(validate_children=False)
    self.update_state(state=states.SUCCESS)
    accumulator = problems.GetAccumulator()
    validations = {key: [{"type": str(type(value)), "dict": value.__dict__}
          for value in getattr(accumulator, key)]
            for key in ["errors", "warnings", "notices"]
    }

#Get dataset end date
    end_date = max([v.end_date for v in schedule.service_periods.itervalues()])

    dataset = app.backend.database['datasets'].find_one(
        {"celery_task_id": self.request.id}
    )

    anomalies = set(dataset['anomalies'])
    if end_date < datetime.date.today().strftime("%Y%m%d"):
        anomalies = anomalies.add("out_of_date")
    elif "out_of_date" in anomalies:
        anomalies = anomalies.remove("out_of_date")
    if anomalies is None:
        anomalies = set()
    app.backend.database['datasets'].find_one_and_update(
        {"celery_task_id": self.request.id},
        {"$set": {"validations": validations,
                  "anomalies": list(anomalies)}}
    )
    return {"validations": validations}
