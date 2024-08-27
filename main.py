from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
import requests
import json
import re
import motor.motor_asyncio
from bson import ObjectId

app = FastAPI()

# MongoDB connection setup
MONGO_DB_URI = 'mongodb+srv://edatech:vp47FCFbbNUosNED@edat.cjietoh.mongodb.net/?retryWrites=true&w=majority&appName=Edat'
#MONGO_DB_URL = "mongodb://localhost:27017"
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_DB_URI)
database = client['test']  # Replace with your database name
exam_questions_collection = database['examquestions']  # Collection name for storing exam questions
student_response_collection = database['studentresponse'] #collection name for storing student response to exam
exam_results_collection = database['examresults'] # Collection name for storing exam results

API_KEY = "dPFNmccRAPS77upmo1mQYcYUFXm3a15z"
ENDPOINT_URL = "https://api.mistral.ai/v1/chat/completions"
MODEL = "mistral-tiny"

def format_math_expression(text: str) -> str:
    # Convert fractions
    text = re.sub(r'(\d+)/(\d+)', r'\\frac{\1}{\2}', text)
    
    # Convert exponents
    text = re.sub(r'(\d+)\^(\d+)', r'\1^{\2}', text)
    
    # Format mathematical symbols
    symbol_map = {
        '×': '\\times',
        '÷': '\\div',
        '±': '\\pm',
        '≠': '\\neq',
        '≤': '\\leq',
        '≥': '\\geq',
        '∞': '\\infty',
        'π': '\\pi',
        '√': '\\sqrt'
    }
    for symbol, latex in symbol_map.items():
        text = text.replace(symbol, latex)
    
    return text


def convert_object_id(document):
    if isinstance(document, dict):
        for key, value in document.items():
            if isinstance(value, ObjectId):
                document[key] = str(value)
            elif isinstance(value, dict):
                convert_object_id(value)
            elif isinstance(value, list):
                for item in value:
                    convert_object_id(item)
    elif isinstance(document, list):
        for item in document:
            convert_object_id(item)
    return document

def api_request(messages: List[Dict[str, str]], max_tokens: int = 2000) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7
    }
    try:
        response = requests.post(ENDPOINT_URL, json=data, headers=headers)
        response.raise_for_status()
        raw_response = response.text #capture raw response 
        print(f'Raw API response: {raw_response}'); 
        try:
            json_response = response.json()  # Attempt to parse JSON
            return format_math_expression(json_response['choices'][0]['message']['content'])
        except json.JSONDecodeError as e: 
            raise HTTPException(status_code=500, detail=f"Failed to parse JSON response: {str(e)} - Raw Response: {raw_response}")

    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"API request failed: {e}")

class ExamRequest(BaseModel):
    exam_board: str
    country: str
    learning_objectives: List[str]
    subject: str
    exam_length: Optional[int] = None
    num_questions: int = 5
    total_marks: Optional[int] = None



@app.post("/generate_exam_questions")
async def generate_exam_questions(request: ExamRequest) -> Dict:
    context = f"""
    Generate {request.num_questions} examination-style questions for the following specifications:
    - Examination Board: {request.exam_board}
    - Country: {request.country}
    - Subject: {request.subject}
    - Learning Objectives: {', '.join(request.learning_objectives)} #gets the learning objectives from the database
    - Number of Questions: {request.num_questions}
    {f'- Examination Length: {request.exam_length} minutes' if request.exam_length else ''}
    {f'- Total Marks: {request.total_marks}' if request.total_marks else ''}

    Requirements:
    1. Questions should follow the {request.exam_board} examination board style and specifications.
    2. Questions can be nested (e.g., 1(a)i, 1(a)ii, 1(b), etc.) as per board expectations.
    3. All questions should be answerable by typing only.
    4. Provide a detailed mark scheme for each question.
    5. Clearly indicate the number of marks for each question or sub-question.
    6. Map each question to the relevant learning objective(s).
    7. Ensure questions and subquestions are unique.
    8. For the mark scheme, ensure you allocate marks for working out or process.
    9. Use proper mathematical notation for fractions, equations, powers, square roots, etc.
    
    Format the output as a JSON object with the following structure:
    {{
        "questions": [
            {{
                "number": "1",
                "text": "Question text",
                "marks": 5,
                "learning_objectives": ["Objective 1", "Objective 2"],
                "mark_scheme": "Detailed mark scheme"
            }},
            ...
        ]
    }}
    """

    messages = [{"role": "user", "content": context}]
    response_text =   api_request(messages, 2000)
    
    try:
        exam_questions = json.loads(response_text)
        # Store exam questions in MongoDB
     
        result = await exam_questions_collection.insert_one(exam_questions)
    
        # Convert ObjectId to string
        exam_questions['_id'] = str(result.inserted_id)
        
        return exam_questions
        
        #return json.loads(response)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse JSON response.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")

class MarkRequest(BaseModel):
    question: Dict
    student_response: str
    student_name: str

@app.post("/mark_student_response")
async def mark_student_response(request: MarkRequest) -> Dict:
    context = f"""
    Mark the following student response based on the given question and mark scheme. Never award marks for things like neatness and presentation:

    Student Name: {request.student_name}
    Question: {request.question['text']}
    Marks available: {request.question['marks']}
    Mark Scheme: {request.question['mark_scheme']}

    Student Response: {request.student_response}

    Please provide:
    1. The marks awarded. Ensure that marks are awarded for only questions they are intended for
    2. Detailed examiner-style feedback, **provide only feedback**. **There never be any salutation e.g. dear ..., hi...**. You can address the student in second person speak using something like 'you'
    3. Justification for the marks given
    
    Format the output as a JSON object with the following structure:
    {{
        "marks_awarded": 0,
        "feedback": "Detailed feedback",
        "justification": "Justification for marks"
    }}
    """

    messages = [{"role": "user", "content": context}]
    response_text = api_request(messages, 1000)
    
    try:
         # Parse the response from the API
        response_data = json.loads(response_text)

        #return json.loads(response_text)

        # Prepare the data to be stored in the database
        student_response_data = {
            "student_name": request.student_name,
            "question": request.question,
            "student_response": request.student_response,
            "marks_awarded": response_data.get("marks_awarded"),
            "feedback": response_data.get("feedback"),
            "justification": response_data.get("justification"),
        }

        # Store the student response in MongoDB
        result = await student_response_collection.insert_one(student_response_data)

        # Convert ObjectId to string for returning to the client
        student_response_data["_id"] = str(result.inserted_id)
        
        return student_response_data
    
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail="Failed to parse JSON response.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")


class ProcessExamRequest(BaseModel):
    exam_questions: Dict
    student_responses: List[str]
    student_name: str

@app.post("/process_exam_responses")
async def process_exam_responses(request: ProcessExamRequest) -> Dict:
    results = {}
    total_marks = 0
    
    # Collect all unique learning objectives
    all_objectives = set()
    for question in request.exam_questions.get('questions', []):
        all_objectives.update(question.get('learning_objectives', []))
    
    marks_per_objective = {obj: 0 for obj in all_objectives}
    total_marks_per_objective = {obj: 0 for obj in all_objectives}

    for question, response in zip(request.exam_questions.get('questions', []), request.student_responses):
        # Handle potential missing keys
        question_number = question.get('number', 'Unknown')
        question_marks = question.get('marks', 0)
        question_objectives = question.get('learning_objectives', [])

        marking_result = await mark_student_response(MarkRequest(
            question=question, student_response=response, student_name=request.student_name))
        results[question_number] = marking_result
        marks_awarded = marking_result.get('marks_awarded', 0)
        total_marks += marks_awarded

        for obj in question_objectives:
            marks_per_objective[obj] += marks_awarded
            total_marks_per_objective[obj] += question_marks

    performance_per_objective = {
        obj: {
            "raw_score": marks_per_objective[obj],
            "total_available": total_marks_per_objective[obj],
            "percentage": (marks_per_objective[obj] / total_marks_per_objective[obj]) * 100 if total_marks_per_objective[obj] > 0 else 0
        } for obj in marks_per_objective
    }

    exam_result =  {
        "student_name": request.student_name,
        "total_marks": total_marks,
        "results_per_question": results,
        "performance_per_objective": performance_per_objective
    } 

 # Save the exam result to MongoDB
    result = await exam_results_collection.insert_one(exam_result)
    
    # Convert ObjectId to string for returning to the client
    exam_result["_id"] = str(result.inserted_id)

    return exam_result
   






