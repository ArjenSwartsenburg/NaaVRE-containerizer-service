FROM condaforge/mambaforge:24.9.2-0 AS build

RUN conda install -c conda-forge conda-pack
COPY environment.yaml .
COPY requirements.txt .

RUN mamba env update --name venv -f environment.yaml

RUN mamba clean --all -y

RUN conda-pack -n venv -o /tmp/env.tar && \
    mkdir /venv && cd /venv && tar xf /tmp/env.tar && \
    rm /tmp/env.tar
RUN /venv/bin/conda-unpack

FROM python:3.14-slim AS runtime

COPY --from=build /venv/ /venv/
ENV PATH=/venv/bin:$PATH
RUN chmod +x /venv/bin/activate
SHELL ["/bin/bash", "-c","/venv/bin/activate"]
RUN echo "source /venv/bin/activate" >> ~/.bashrc
SHELL ["/bin/bash", "--login", "-c"]

COPY requirements.txt .

# RUN pip install -r requirements.txt

WORKDIR /code

COPY ./app /code/app

# Exec-form CMD with an absolute path: the bash --login SHELL above sources
# /etc/profile, which resets PATH and would make a shell-form `fastapi` exit
# 127. Port 8000 is what the helm chart's service and probes expect.
CMD ["/venv/bin/fastapi", "run", "app/main.py", "--port", "8000"]
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--log-level", "trace"]
