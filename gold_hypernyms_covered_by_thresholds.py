import sys

def main (goldFile, cosineFile):
    with open (goldFile) as gold_file, open (cosineFile) as cosine_file:
          hypernymList = []
          for line in cosine_file:
               hyponym, hypernym, cosine_score = line.strip().split("\t")
               hypernymList.append(hypernym)

          goldList = []
          for line in gold_file:
              hypernyms = line.strip().split("\t")
              for hypernym in hypernyms:
                  # if hypernym in hypernymList:
                  goldList.append(hypernym)
          print(goldList)
          print(len(goldList))

if __name__ == '__main__':
    goldFile = sys.argv[1]
    cosineFile = sys.argv[2]
    main(goldFile, cosineFile)
