# Let Them Draw - Receptionist

This is the serverless backend for the Let Them Draw app that handles the requests from the [frontend](https://github.com/danielwohlgemuth/let-them-draw-website).

More details about the app and its architecture are available at [let-them-draw-infrastructure](https://github.com/danielwohlgemuth/let-them-draw-infrastructure).

## API

The API is built with [FastAPI](https://fastapi.tiangolo.com/), a Python web framework for building APIs. The Magnum library is used to make FastAPI compatible with AWS Lambda.

![receptionist api](/assets/receptionist-api.png)

The documentation for the API is available under the `/api/docs` path of the frontend in a development environment.